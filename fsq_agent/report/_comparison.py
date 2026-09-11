# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import difflib
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from fsq_agent.models import PublicRunReport, ReportGenerationError


def normalize_snapshot(text: str) -> str:
    try:
        value = json.loads(text)
    except ValueError:
        value = text
    if isinstance(value, dict) and isinstance(value.get("xml"), str):
        metadata = {key: item for key, item in value.items() if key != "xml"}
        normalized_xml = normalize_snapshot(value["xml"])
        return (json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n\n" if metadata else "") + normalized_xml
    if isinstance(value, dict) and isinstance(value.get("snapshot"), str):
        metadata = {key: item for key, item in value.items() if key != "snapshot"}
        header = json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) if metadata else ""
        return header + "\n\n" + value["snapshot"].replace("\r\n", "\n")
    if isinstance(value, str) and value.lstrip().startswith("<"):
        if "<!DOCTYPE" in value.upper() or "<!ENTITY" in value.upper():
            return value
        try:
            tree = ET.fromstring(value)  # noqa: S314 - bounded snapshot, declarations/entities rejected above.
            if any(node.attrib.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve" for node in tree.iter()):
                return value
            ET.indent(tree, space="  ")
            return ET.tostring(tree, encoding="unicode")
        except ET.ParseError:
            return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) if not isinstance(value, str) else value.replace("\r\n", "\n")


def snapshot_diff(before: dict | None, after: dict | None) -> dict:
    result = {"kind": "before_after", "algorithm": "sequence-matcher/v1", "normalization": "fsq.snapshot-text/v1", "before": _ref(before), "after": _ref(after), "rows": []}
    if not before or not after or before.get("availability") != "available" or after.get("availability") != "available":
        return {**result, "status": "unavailable", "reason": "missing_comparison_input"}
    if any(
        item.get("truncated")
        or item.get("transformed")
        or item.get("redacted")
        or (item.get("coverage") or {}).get("status") in {"partial", "unknown", "clipped", "truncated"}
        or item.get("compaction")
        for item in (before, after)
    ):
        return {**result, "status": "incomplete", "reason": "partial_or_transformed_input"}
    if not isinstance(before.get("content"), str) or not isinstance(after.get("content"), str):
        return {**result, "status": "unavailable", "reason": "snapshot_content_unavailable"}
    left = normalize_snapshot(before["content"]).splitlines()
    right = normalize_snapshot(after["content"]).splitlines()
    rows = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, left, right, autojunk=False).get_opcodes():
        for offset in range(max(i2 - i1, j2 - j1)):
            old = left[i1 + offset] if i1 + offset < i2 else None
            new = right[j1 + offset] if j1 + offset < j2 else None
            segments = _segments(old or "", new or "")
            rows.append(
                {
                    "kind": "context" if tag == "equal" else "added" if old is None else "removed" if new is None else "changed",
                    "before": old,
                    "after": new,
                    "before_number": i1 + offset + 1 if old is not None else None,
                    "after_number": j1 + offset + 1 if new is not None else None,
                    **segments,
                }
            )
    return {**result, "status": "unchanged" if left == right else "changed", "rows": rows}


def _ref(artifact):
    return {key: artifact.get(key) for key in ("run_id", "artifact_id", "sha256")} if artifact else None


def _segments(before, after):
    prefix = 0
    while prefix < min(len(before), len(after)) and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    while suffix < min(len(before) - prefix, len(after) - prefix) and before[-suffix - 1] == after[-suffix - 1]:
        suffix += 1
    result = {}
    for side, text in (("before", before), ("after", after)):
        result[f"{side}_segments"] = [
            {"text": text[:prefix], "changed": False},
            {"text": text[prefix : len(text) - suffix if suffix else len(text)], "changed": before != after},
            {"text": text[-suffix:] if suffix else "", "changed": False},
        ]
    return result


def step_diffs(report: PublicRunReport) -> list[dict]:
    result = []
    for step in report.steps:
        artifacts = [
            item
            for item in report.artifacts
            if item.get("run_id") == step.get("run_id", report.run["run_id"])
            and step["step_execution_id"] is not None
            and item.get("step_execution_id") == step["step_execution_id"]
            and item.get("kind") == "ui_snapshot"
        ]
        before = next((item for item in artifacts if item.get("phase") in {"prepare", "before"}), None)
        after = next((item for item in reversed(artifacts) if item.get("phase") in {"finalize", "after"}), None)
        if artifacts:
            result.append({"run_id": report.run["run_id"], "step_execution_id": step["step_execution_id"], **snapshot_diff(before, after)})
    return result


def compare_reports(current: PublicRunReport, baseline: PublicRunReport) -> dict:
    same_platform = current.run.get("platform") == baseline.run.get("platform")
    current_digest = verified_source_digest(current)
    baseline_digest = verified_source_digest(baseline)
    mapping = _recording_mapping(current, baseline)
    if not same_platform or not ((current_digest and current_digest == baseline_digest) or mapping):
        raise ReportGenerationError("Runs do not have comparable source identities.", context={"reason": "baseline_incomparable"})
    old = {}
    for step in baseline.steps:
        key = _step_key(step)
        if key is not None:
            old.setdefault(key, []).append(step)
    matched = set()
    rows = []
    for step in current.steps:
        mapped = mapping.get(_step_key(step)) if mapping else None
        if mapped:
            candidates = [item for item in baseline.steps if item["step_execution_id"] == mapped]
            key = None
        else:
            key = _step_key(step)
            candidates = old.get(key, [])
        prior = candidates[0] if len(candidates) == 1 else None
        if prior:
            matched.add(prior["step_execution_id"])
        before = _after_artifact(baseline, prior)
        after = _after_artifact(current, step)
        rows.append(
            {
                "current": {"run_id": current.run["run_id"], "step_execution_id": step["step_execution_id"]},
                "baseline": {"run_id": baseline.run["run_id"], "step_execution_id": prior["step_execution_id"]} if prior else None,
                "status": "matched" if prior else "unmatched",
                "snapshot": {**snapshot_diff(before, after), "kind": "baseline_current"},
            }
        )
    return {
        "kind": "baseline_current",
        "status": "comparable",
        "baseline_run_id": baseline.run["run_id"],
        "current_run_id": current.run["run_id"],
        "steps": rows,
        "unmatched_baseline": [{"run_id": baseline.run["run_id"], "step_execution_id": step["step_execution_id"]} for step in baseline.steps if step["step_execution_id"] not in matched],
    }


def _source_digest(report):
    source = report.source
    return source.get("sha256") or source.get("digest") or source.get("snapshot_sha256") or source.get("case_sha256")


def verified_source_digest(report):
    claimed = _source_digest(report)
    artifact = next(
        (item for item in report.artifacts if item.get("run_id") == report.run["run_id"] and item.get("artifact_id") == "source-snapshot" and item.get("availability") == "available"), None
    )
    if not claimed or artifact is None or artifact.get("sha256") != claimed:
        return None
    root = report._run_dirs.get(report.run["run_id"])
    if root is None:
        return None
    path = Path(root) / str(artifact.get("path"))
    try:
        if path.is_symlink() or not path.resolve().is_relative_to(Path(root).resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != claimed:
            return None
    except OSError:
        return None
    return claimed


def _step_key(step):
    source = step.get("source_step_id")
    occurrence = step.get("invocation_path")
    return (str(source), str(occurrence or "root")) if source is not None else None


def _recording_mapping(current, baseline):
    relations = [current.lineage, *current.lineage.get("relations", [])]
    relations.extend(
        relation for relation in [baseline.lineage, *baseline.lineage.get("relations", [])] if relation.get("kind") == "recording" and relation.get("originating_run_id") == baseline.run["run_id"]
    )
    for lineage in relations:
        if (lineage.get("case_digest") or lineage.get("case_sha256")) != verified_source_digest(current):
            continue
        candidate_digest = lineage.get("candidate_digest") or verified_source_digest(current)
        candidate = next(
            (item for item in baseline.artifacts if item.get("artifact_id") == "candidate_case" and item.get("sha256") == candidate_digest and item.get("availability") == "available"),
            None,
        )
        if candidate is None:
            continue
        root = baseline._run_dirs.get(baseline.run["run_id"])
        candidate_path = Path(root) / str(candidate.get("path")) if root else None
        try:
            retained_bytes = candidate_path.read_bytes() if candidate_path else b""
            if (
                candidate_path is None
                or candidate_path.is_symlink()
                or not candidate_path.resolve().is_relative_to(Path(root).resolve())
                or hashlib.sha256(retained_bytes).hexdigest() != candidate.get("sha256")
            ):
                continue
        except OSError:
            continue
        try:
            recording_path = Path(root) / "recording.json"
            recording = json.loads(recording_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            recording = {}
        records = lineage.get("command_mapping") or recording.get("command_mapping")
        if not isinstance(records, list):
            continue
        _validate_retained_mapping(recording, records, baseline, lineage)
        if lineage.get("candidate_digest"):
            _validate_saved_case(lineage, retained_bytes, current, baseline, records)
        mapping = {}
        for item in records:
            if isinstance(item, dict) and item.get("step_execution_id"):
                for step in current.steps:
                    source = str(step.get("source_step_id") or "")
                    exact = item.get("current_source_step_id")
                    occurrence = item.get("current_invocation_path")
                    if exact is not None:
                        matches = source == exact and (step.get("invocation_path") == occurrence if occurrence is not None else step.get("root_invocation") is True)
                    else:
                        matches = step.get("root_invocation") is True and isinstance(item.get("command_index"), int) and step.get("source_index") == item["command_index"]
                    if matches:
                        if step.get("root_invocation") is not True or step.get("source_index") != item.get("command_index"):
                            raise ReportGenerationError("Current recording mapping conflicts with its retained command index.", context={"reason": "lineage_invalid"})
                        mapping[_step_key(step)] = item["step_execution_id"]
        if mapping:
            return mapping
    return {}


def _validate_saved_case(lineage, candidate_bytes, current, baseline, records):
    import yaml

    from fsq_agent.report._run_report import contained_file

    artifact = next((item for item in baseline.artifacts if item.get("artifact_id") == "saved-case-" + str(lineage.get("case_digest")) and item.get("availability") == "available"), None)
    if artifact is None or artifact.get("path") != lineage.get("saved_snapshot_path") or artifact.get("sha256") != verified_source_digest(current):
        raise ReportGenerationError("Saved Case identity is unavailable.", context={"reason": "lineage_invalid"})
    try:
        saved = contained_file(baseline._run_dirs[baseline.run["run_id"]], artifact["path"]).read_bytes()
        if hashlib.sha256(saved).hexdigest() != artifact["sha256"]:
            raise ValueError("Saved Case changed")  # noqa: TRY301
        candidate_documents = list(yaml.safe_load_all(candidate_bytes))
        saved_documents = list(yaml.safe_load_all(saved))
        if len(candidate_documents) != 2 or len(saved_documents) != 2 or not isinstance(candidate_documents[0], dict) or not isinstance(saved_documents[0], dict):
            raise ValueError("Invalid Case documents")  # noqa: TRY301
        candidate_documents[0].pop("name", None)
        saved_documents[0].pop("name", None)
        if candidate_documents != saved_documents or not isinstance(saved_documents[1], list):
            raise ValueError("Changed Case commands or configuration")  # noqa: TRY301
        if [record.get("command_index") for record in records] != list(range(len(saved_documents[1]))):
            raise ValueError("Incomplete saved command mapping")  # noqa: TRY301
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ReportGenerationError("Saved Case mapping does not match retained source.", context={"reason": "lineage_invalid"}) from exc


def _validate_retained_mapping(recording, records, baseline, lineage):
    retained = recording.get("command_mapping") if isinstance(recording, dict) else None
    if (recording.get("source_run_id") if isinstance(recording, dict) else None) != baseline.run["run_id"] or not isinstance(retained, list):
        raise ReportGenerationError("Run-local recording metadata does not identify the originating Run.", context={"reason": "lineage_invalid"})
    if lineage.get("originating_run_id") != baseline.run["run_id"]:
        raise ReportGenerationError("Recording lineage does not identify the originating Run.", context={"reason": "lineage_invalid"})
    keys = ("command_index", "source_step_id", "step_execution_id", "invocation_path")
    for record in records:
        matches = [item for item in retained if isinstance(item, dict) and item.get("command_index") == record.get("command_index")]
        if len(matches) != 1 or any(record.get(key) != matches[0].get(key) for key in keys):
            raise ReportGenerationError("Recording mapping conflicts with retained Case bytes.", context={"reason": "lineage_invalid"})
        origins = [step for step in baseline.steps if step.get("step_execution_id") == record.get("step_execution_id")]
        if len(origins) != 1 or origins[0].get("source_step_id") != record.get("source_step_id") or origins[0].get("invocation_path") != record.get("invocation_path"):
            raise ReportGenerationError("Recording mapping conflicts with its originating execution identity.", context={"reason": "lineage_invalid"})


def _after_artifact(report, step):
    if step is None:
        return None
    return next(
        (
            item
            for item in reversed(report.artifacts)
            if item.get("run_id") == report.run["run_id"]
            and item.get("kind") == "ui_snapshot"
            and step["step_execution_id"] is not None
            and item.get("step_execution_id") == step["step_execution_id"]
            and item.get("phase") in {"finalize", "after"}
        ),
        None,
    )
