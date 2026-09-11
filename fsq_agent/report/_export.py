# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import copy
import hashlib
import io
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageDraw

from fsq_agent.models import PublicRunReport, ReportGenerationError, RunReportExportOptions, RunReportExportResult
from fsq_agent.report._comparison import normalize_snapshot, step_diffs
from fsq_agent.report._junit import render_junit
from fsq_agent.report._run_report import (
    MAX_BUNDLE_BYTES,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    MAX_SNAPSHOT_BYTES,
    MAX_TEXT_BYTES,
    contained_file,
    digest_file,
    enforce_report_budget,
    report_error,
    sanitize_content,
)

TEXT_SUFFIXES = {".json", ".txt", ".xml", ".md", ".yaml", ".yml", ".jsonl"}
DISPLAY_KEYS = {"summary", "description", "goal_summary", "message", "label", "error_message", "explanation", "prompt", "content", "outcome_description"}


def export_report(report: PublicRunReport, options: RunReportExportOptions, *, replace_derived=False) -> RunReportExportResult:
    destination, roots, transformed, output = prepare_export(report, options, replace_derived=replace_derived)
    _verify_snapshot(report, roots)
    _atomic_write(destination, output, roots=roots, replace_derived=replace_derived)
    entry = {
        "file_id": "report",
        "path": destination.name,
        "size_bytes": len(output),
        "sha256": hashlib.sha256(output).hexdigest(),
        "mime_type": {"json": "application/json", "junit": "application/xml", "html": "text/html", "bundle": "application/zip"}[options.format],
    }
    return RunReportExportResult(export_id=options.export_id, format=options.format, path=destination, files=(entry,), warnings=tuple(transformed.warnings))


def prepare_export(report, options, *, replace_derived=False):
    from pydantic import ValidationError

    try:
        checked = PublicRunReport.model_validate(report.model_dump(mode="json"))
        for nested in _report_graphs(checked.model_dump(mode="json")):
            PublicRunReport.model_validate(nested)
    except ValidationError as exc:
        raise report_error("schema_invalid", "Public report is invalid or has inconsistent conclusions.") from exc
    checked._run_dirs = report._run_dirs
    checked._fingerprints = report._fingerprints
    checked._related = report._related
    report = checked
    legacy_rebuild = False
    if replace_derived and options.format == "html" and len(report._run_dirs) == 1:
        run_id = report.run["run_id"]
        root = report._run_dirs.get(run_id)
        if root is not None:
            metadata_path = contained_file(Path(root), "run.json")
            if metadata_path.is_file():
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                source = metadata.get("source", {})
                legacy_rebuild = (
                    metadata.get("schema_version") == "fsq.run/v1"
                    and metadata.get("run_id") == run_id == Path(root).name
                    and metadata.get("platform") == report.run.get("platform")
                    and isinstance(source, dict)
                    and source.get("kind") in {"goal", "case"}
                    and not source.get("snapshot_path")
                    and not source.get("digest")
                    and not report.comparison.get("baseline_report")
                    and not report.lineage.get("related_runs")
                    and options.share_profile is None
                )
            else:
                historical_files = {"core-report.json", "report.json", "report-fallback.json", "evidence-manifest.json", "evidence-events.jsonl", "events.jsonl"}
                fingerprints = report._fingerprints.get(run_id, {})
                legacy_rebuild = (
                    run_id == Path(root).name
                    and any(fingerprints.get(name) for name in historical_files)
                    and not report.comparison.get("baseline_report")
                    and not report.lineage.get("related_runs")
                    and options.share_profile is None
                )
    if legacy_rebuild:
        report.warnings.append("Historical Run source snapshot and digest are unavailable; this is a local compatibility view, not a verified source export.")
    else:
        _require_trustworthy_source_identity(report)
    destination = options.destination.expanduser().absolute()
    roots = {**report._run_dirs, **options.run_dirs}
    _destination(destination, roots, replace_derived)
    destination = destination.resolve()
    _verify_snapshot(report, roots)
    try:
        transformed, contents = _prepare(report, roots, options)
    except ReportGenerationError:
        raise
    except (OSError, ValueError) as exc:
        raise report_error("artifact_unavailable", "Report artifacts could not be read safely.") from exc
    enforce_report_budget(transformed)
    from fsq_agent.report._static_html import render_html

    if options.format == "bundle":
        _set_export_paths(transformed, contents)
    if options.format in {"html", "bundle"}:
        enforce_report_budget(transformed, rendered_size=lambda value: len(render_html(value, {}).encode("utf-8")))
    if options.format in {"junit", "bundle"}:
        enforce_report_budget(transformed, rendered_size=lambda value: len(render_junit(value)))
    junit_bytes = render_junit(transformed) if options.format in {"junit", "bundle"} else b""
    html_bytes = render_html(transformed, contents).encode("utf-8") if options.format in {"html", "bundle"} else b""
    json_bytes = transformed.model_dump_json(indent=2).encode("utf-8")
    outputs = {"json": json_bytes, "junit": junit_bytes, "html": html_bytes}
    if options.format == "bundle":
        files = {"report.html": html_bytes, "report.json": json_bytes, "junit.xml": junit_bytes}
        for (run_id, artifact_id), content in contents.items():
            artifact = next(item for item in transformed.artifacts if item["run_id"] == run_id and item["artifact_id"] == artifact_id)
            entry_path = artifact["export_path"]
            if entry_path in files:
                raise report_error("schema_invalid", "Qualified artifact export path collision.")
            files[entry_path] = content
        if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
            raise report_error("resource_limit", "Bundle exceeds the fixed uncompressed resource limit.")
        index = {
            "schema_version": "fsq.export/v1",
            "export_id": options.export_id,
            "run_id": report.run["run_id"],
            "files": [{"path": name, "sha256": hashlib.sha256(value).hexdigest(), "size_bytes": len(value)} for name, value in files.items()],
            "artifacts": transformed.artifacts,
            "profile_version": options.share_profile.schema_version if options.share_profile else None,
            "transformations": transformed.run.get("export", {}).get("transformations", []),
            "omissions": transformed.run.get("export", {}).get("omissions", []),
        }
        files["index.json"] = json.dumps(index, indent=2, ensure_ascii=False).encode()
        files["SHA256SUMS"] = "\n".join(f"{hashlib.sha256(value).hexdigest()}  {name}" for name, value in files.items()).encode()
        if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
            raise report_error("resource_limit", "Complete bundle including index/checksums exceeds its resource limit.")
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in files.items():
                archive.writestr(name, content)
        output = buffer.getvalue()
    else:
        output = outputs[options.format]
    return destination, roots, transformed, output


def _require_trustworthy_source_identity(report: PublicRunReport) -> None:
    for graph in _report_graphs(report.model_dump(mode="json")):
        try:
            run_id = graph.get("run", {}).get("run_id")
            source = graph.get("source", {})
            path_value = source.get("snapshot_path")
            digest = source.get("digest")
            root = report._run_dirs.get(run_id) if isinstance(run_id, str) else None
            root_path = Path(root)
            artifacts = [item for item in graph.get("artifacts", []) if item.get("run_id") == run_id and item.get("artifact_id") == "source-snapshot"]
            trustworthy = (
                isinstance(run_id, str)
                and bool(run_id)
                and isinstance(path_value, str)
                and bool(path_value)
                and isinstance(digest, str)
                and len(digest) == 64
                and root_path.name == run_id
                and len(artifacts) == 1
                and artifacts[0].get("availability") == "available"
                and artifacts[0].get("path") == path_value
                and artifacts[0].get("sha256") == digest
            )
            path = contained_file(root_path, path_value)
            trustworthy = trustworthy and path.is_file() and digest_file(path) == digest
            metadata_path = contained_file(root_path, "run.json")
            if metadata_path.exists():
                persisted = json.loads(metadata_path.read_text(encoding="utf-8"))
                persisted_source = persisted.get("source", {}) if isinstance(persisted, dict) else {}
                trustworthy = (
                    trustworthy
                    and isinstance(persisted_source, dict)
                    and persisted.get("run_id") == run_id
                    and persisted_source.get("snapshot_path") == path_value
                    and persisted_source.get("digest") == digest
                )
        except (AttributeError, OSError, TypeError, ValueError, ReportGenerationError):
            trustworthy = False
        if not trustworthy:
            raise report_error("source_identity_unavailable", "Retained source identity is unavailable or untrustworthy.")


def _set_export_paths(report, contents):
    path_counts = {}
    for artifact in report.artifacts:
        key = (artifact["run_id"], artifact["artifact_id"])
        if key in contents:
            path_key = (artifact["run_id"], artifact["path"])
            path_counts[path_key] = path_counts.get(path_key, 0) + 1
    used = set()
    for artifact in report.artifacts:
        key = (artifact["run_id"], artifact["artifact_id"])
        if key not in contents:
            continue
        path_key = (artifact["run_id"], artifact["path"])
        if path_counts[path_key] == 1:
            target = f"runs/{artifact['run_id']}/{artifact['path']}"
        else:
            qualified = hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
            target = f"runs/{artifact['run_id']}/by-artifact/{qualified}/{Path(artifact['path']).name}"
        if target in used:
            raise report_error("schema_invalid", "Artifact export identity collided.")
        used.add(target)
        artifact["export_path"] = target
    values = report.model_dump(mode="json")
    _sync_graph_artifacts(values, {(item["run_id"], item["artifact_id"]): item for item in report.artifacts}, False)
    report.comparison = values["comparison"]
    report.lineage = values["lineage"]


def _destination(destination, roots, replace_derived):
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise report_error("path_unsafe", "Export destination contains a symlink.")
    destination = destination.resolve()
    if destination.exists() and not replace_derived:
        raise report_error("destination_exists", "Export destination already exists.")
    for root in roots.values():
        resolved = Path(root).resolve()
        if destination.is_relative_to(resolved) and not destination.is_relative_to(resolved / "exports") and not (replace_derived and destination == resolved / "report.html"):
            raise report_error("path_unsafe", "Export cannot overwrite or add authoritative Run files.")


def _verify_snapshot(report, roots):
    for run_id, entries in report._fingerprints.items():
        root = roots.get(run_id)
        if root is None or Path(root).resolve() != report._run_dirs.get(run_id):
            raise report_error("source_changed", "Report source directory identity changed.")
        for name, expected in entries.items():
            path = contained_file(Path(root), name)
            actual = digest_file(path) if path.is_file() else None
            if actual != expected:
                raise report_error("source_changed", "Persisted report source changed during export.")


def _prepare(report, roots, options):
    transformed = report.model_copy(deep=True)
    for other in report._related:
        transformed.artifacts.extend(copy.deepcopy(other.artifacts))
    graphs = list(_report_graphs(transformed.model_dump(mode="json")))
    artifact_index = {}
    for graph in graphs:
        for artifact in graph["artifacts"]:
            artifact_index.setdefault((artifact["run_id"], artifact["artifact_id"]), artifact)
    transformed.artifacts = list(artifact_index.values())
    profile = options.share_profile
    transformations = []
    omissions = []
    if profile:
        values = transformed.model_dump(mode="json")
        _replace_display(values, profile.replacements, skip_content=True, inventory=transformations)
        _remove_graph_fields(values, profile.remove_fields, omissions)
        transformed = PublicRunReport.model_validate(values)
    selected = {(ref.run_id, ref.artifact_id) for ref in profile.artifacts} if profile and profile.artifacts is not None else None
    known = {(item["run_id"], item["artifact_id"]) for item in transformed.artifacts}
    references = set(selected or ()) | ({(mask.run_id, mask.artifact_id) for mask in profile.masks} if profile else set())
    if not references.issubset(known):
        raise report_error("profile_invalid", "Share profile refers to an unknown qualified artifact.")
    if profile:
        by_identity = {(item["run_id"], item["artifact_id"]): item for item in transformed.artifacts}
        for mask in profile.masks:
            target = by_identity[(mask.run_id, mask.artifact_id)]
            if target.get("kind") != "screenshot" or target.get("availability") != "available" or not target.get("path") or (selected is not None and (mask.run_id, mask.artifact_id) not in selected):
                raise report_error("profile_invalid", "Every mask must select an available screenshot artifact.")
    contents = {}
    total = 0
    display_text_bytes = 0
    for artifact in transformed.artifacts:
        key = (artifact["run_id"], artifact["artifact_id"])
        if selected is not None and key not in selected:
            artifact.update(availability="omitted", unavailable_reason="share_profile_selection")
            artifact.pop("content", None)
            artifact.pop("normalized_content", None)
            omissions.append({"run_id": key[0], "artifact_id": key[1], "reason": "share_profile_selection"})
            continue
        if artifact.get("availability") != "available" or not artifact.get("path"):
            if selected is not None and key in selected and options.format == "bundle":
                raise report_error("artifact_unavailable", "Selected bundle artifact is unavailable.")
            continue
        root = roots.get(key[0])
        if root is None:
            raise report_error("path_unsafe", "Artifact Run directory was not supplied.")
        path = contained_file(Path(root), artifact["path"])
        size = path.stat().st_size
        total += size
        if total > MAX_BUNDLE_BYTES:
            raise report_error("resource_limit", "Selected artifacts exceed the bundle resource limit.")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != artifact.get("sha256"):
            raise report_error("source_changed", "Artifact changed during export.")
        masks = [mask for mask in profile.masks if (mask.run_id, mask.artifact_id) == key] if profile else []
        if artifact["kind"] == "screenshot":
            try:
                content = _raster(content, masks)
            except (OSError, ValueError, Image.DecompressionBombError) as exc:
                if masks or (options.format == "bundle" and selected is not None):
                    raise report_error("artifact_unavailable", "Selected raster cannot be safely exported.") from exc
                artifact.update(availability="omitted", unavailable_reason="unsafe_or_oversized_raster")
                transformed.warnings.append(f"Raster omitted: {key[0]}/{key[1]}.")
                continue
        elif path.suffix.casefold() in TEXT_SUFFIXES:
            try:
                text = content.decode("utf-8")
            except UnicodeError as exc:
                if options.format == "bundle":
                    raise report_error("artifact_unavailable", "Selected text artifact is not UTF-8.") from exc
                continue
            is_case = artifact.get("source_kind") == "case" or path.suffix.casefold() in {".yaml", ".yml"}
            format_kind = "yaml" if is_case else "jsonl" if path.suffix.casefold() == ".jsonl" else "json" if path.suffix.casefold() == ".json" else "text"
            if format_kind == "jsonl":
                text, tail_bytes = _complete_jsonl_prefix(text)
                if tail_bytes:
                    artifact.update(truncated=True, omitted_tail_bytes=tail_bytes, unavailable_reason="incomplete_trailing_record")
                    omissions.append({"run_id": key[0], "artifact_id": key[1], "reason": "incomplete_trailing_record", "omitted_bytes": tail_bytes})
                    transformed.warnings.append(f"Incomplete trailing JSONL record omitted from {key[0]}/{key[1]}.")
            text = sanitize_content(text, format_kind=format_kind, replacements=profile.replacements if profile else (), remove_content=bool(profile and "artifacts.content" in profile.remove_fields))
            content = text.encode("utf-8")
            if "content" in artifact:
                remaining = min(MAX_SNAPSHOT_BYTES, max(0, MAX_TEXT_BYTES - display_text_bytes))
                display = content[:remaining].decode("utf-8", errors="replace")
                display_text_bytes += len(display.encode("utf-8"))
                artifact["content"] = display
                if len(content) > remaining:
                    artifact.update(truncated=True, unavailable_reason="display_limit")
                if artifact["kind"] in {"ui_snapshot", "ui_tree"}:
                    artifact["normalized_content"] = normalize_snapshot(artifact["content"])
        else:
            artifact.update(availability="omitted", unavailable_reason="unsupported_export_type")
            if options.format == "bundle" and selected is not None:
                raise report_error("profile_invalid", "Selected bundle artifact type is unsupported.")
            continue
        digest = hashlib.sha256(content).hexdigest()
        if digest != artifact["sha256"]:
            artifact.update(transformed=True, derived_sha256=digest, derived_size_bytes=len(content))
            transformations.append(
                {
                    "run_id": key[0],
                    "artifact_id": key[1],
                    "operations": (["rectangle_mask"] if masks else ["literal_text_or_display_redaction"] if artifact["kind"] != "screenshot" else ["raster_normalization"]),
                    "source_sha256": artifact["sha256"],
                    "retained_sha256": digest,
                }
            )
        contents[key] = content
    transformed.comparison["before_after"] = step_diffs(transformed)
    if profile:
        if transformations or omissions:
            transformed.warnings.append("Share profile transformed export copies; source facts were preserved.")
        declaration = profile.case_review_declaration
        if declaration:
            artifact = next((item for item in transformed.artifacts if (item["run_id"], item["artifact_id"]) == (declaration.run_id, declaration.artifact_id)), None)
            if (
                artifact is None
                or artifact.get("availability") != "available"
                or (artifact.get("derived_sha256") or artifact.get("sha256")) != declaration.sha256
                or (artifact.get("source_kind") != "case" and not str(artifact.get("path", "")).endswith((".yaml", ".yml")))
            ):
                raise report_error("profile_invalid", "Case review declaration does not match retained Case evidence.")
            transformed.lineage["case_review_declaration"] = {**declaration.model_dump(), "kind": "user_declaration_for_export", "exported_at": datetime.now(UTC).isoformat()}
        transformed.comparison["before_after"] = step_diffs(transformed)
        if "baseline_current" in transformed.comparison and (profile.replacements or profile.remove_fields or profile.artifacts is not None):
            transformed.comparison["baseline_current"] = {
                "kind": "baseline_current",
                "status": "incomplete",
                "reason": "share_transformed_comparison_inputs",
                "baseline_run_id": transformed.comparison["baseline_current"].get("baseline_run_id"),
            }
    transformed.run["export"] = {"profile_version": profile.schema_version if profile else None, "transformations": transformations, "omissions": omissions}
    graph = transformed.model_dump(mode="json")
    _sync_graph_artifacts(
        graph, {(item["run_id"], item["artifact_id"]): item for item in transformed.artifacts}, bool(profile and (profile.replacements or profile.remove_fields or profile.artifacts is not None))
    )
    transformed = PublicRunReport.model_validate(graph)
    return transformed, contents


def _report_graphs(value):
    if not isinstance(value, dict):
        return
    if value.get("schema_version") == "fsq.report/v1":
        yield value
        baseline = value.get("comparison", {}).get("baseline_report")
        if isinstance(baseline, dict):
            yield from _report_graphs(baseline)
        for related in value.get("lineage", {}).get("related_runs", []):
            yield from _report_graphs(related)


def _remove_graph_fields(graph, fields, omissions):
    for report in _report_graphs(graph):
        for field in fields:
            if field == "artifacts.content":
                continue
            section, name = field.split(".")
            targets = report.get(section, {})
            for target in targets if isinstance(targets, list) else [targets]:
                if isinstance(target, dict) and name in target:
                    target[name] = "[OMITTED]"
                    omissions.append({"run_id": report["run"]["run_id"], "kind": "display_field", "field": field})
            if field == "steps.error_message":
                failures = [report["execution"].get("first_failure"), *report["execution"].get("recovered_failures", [])]
                for failure in failures:
                    if isinstance(failure, dict) and "error_message" in failure:
                        failure["error_message"] = "[OMITTED]"
                for interpretation in report["execution"].get("interpretations", []):
                    if interpretation.get("kind") == "recorded_fact":
                        interpretation["text"] = "[OMITTED]"


def _sync_graph_artifacts(graph, artifacts, transformed_inputs):
    for report in _report_graphs(graph):
        report["artifacts"] = [copy.deepcopy(artifacts[(artifact["run_id"], artifact["artifact_id"])]) for artifact in report["artifacts"]]
        model = PublicRunReport.model_validate(report)
        report["comparison"]["before_after"] = step_diffs(model)
        if transformed_inputs and "baseline_current" in report["comparison"]:
            report["comparison"]["baseline_current"] = {
                "kind": "baseline_current",
                "status": "incomplete",
                "reason": "share_transformed_comparison_inputs",
                "baseline_run_id": report["comparison"]["baseline_current"].get("baseline_run_id"),
            }


def _replace_display(value, replacements, *, skip_content=False, inventory=None, run_id=None, path=""):
    if isinstance(value, dict):
        if value.get("schema_version") == "fsq.report/v1":
            run_id = value.get("run", {}).get("run_id")
            path = ""
        for key, child in value.items():
            field_path = f"{path}.{key}" if path else key
            display_key = key in DISPLAY_KEYS or (key == "text" and value.get("kind") in {"recorded_fact", "deterministic_classification", "ai_suggestion"})
            if display_key and isinstance(child, str) and not (skip_content and key in {"content", "normalized_content"}):
                text = child
                for replacement in replacements:
                    text = text.replace(replacement.text, replacement.replacement)
                value[key] = text
                if text != child and inventory is not None:
                    inventory.append({"run_id": run_id, "field_path": field_path, "operation": "literal_replacement"})
            elif isinstance(child, (dict, list)):
                _replace_display(child, replacements, skip_content=skip_content, inventory=inventory, run_id=run_id, path=field_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _replace_display(child, replacements, skip_content=skip_content, inventory=inventory, run_id=run_id, path=f"{path}[{index}]")


def _complete_jsonl_prefix(text):
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            json.loads(line)
        except ValueError as exc:
            if index == len(lines) - 1 and not line.endswith(("\n", "\r")):
                return "".join(lines[:index]), len(line.encode("utf-8"))
            raise report_error("artifact_unavailable", "Optional log contains an invalid complete record.") from exc
    return text, 0


def _raster(content, masks):
    if len(content) > MAX_IMAGE_BYTES:
        raise ValueError("Raster byte limit exceeded.")
    with Image.open(io.BytesIO(content)) as image:
        if image.width * image.height > MAX_IMAGE_PIXELS or image.format not in {"PNG", "JPEG", "WEBP"}:
            raise ValueError("Raster dimensions or type are unsupported.")
        image.load()
        raster = image.convert("RGB")
    drawing = ImageDraw.Draw(raster)
    for mask in masks:
        if mask.x + mask.width > raster.width or mask.y + mask.height > raster.height:
            raise ValueError("Mask exceeds image bounds.")
        drawing.rectangle((mask.x, mask.y, mask.x + mask.width - 1, mask.y + mask.height - 1), fill="black")
    output = io.BytesIO()
    raster.save(output, format="PNG")
    return output.getvalue()


def _atomic_write(destination, content, *, roots, replace_derived=False):
    temporary = None
    try:
        _destination(destination, roots, replace_derived)
        destination.parent.mkdir(parents=True, exist_ok=True)
        _destination(destination, roots, replace_derived)
        parent = destination.parent.stat()
        descriptor, temporary = tempfile.mkstemp(prefix=".fsq-export-", dir=destination.parent)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _destination(destination, roots, replace_derived)
        current_parent = destination.parent.stat()
        if (parent.st_dev, parent.st_ino) != (current_parent.st_dev, current_parent.st_ino):
            raise report_error("source_changed", "Export parent identity changed before commit.")
        if replace_derived:
            Path(temporary).replace(destination)
        else:
            os.link(temporary, destination)
    except OSError as exc:
        raise report_error("persistence_failed", "Atomic report export failed.") from exc
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
