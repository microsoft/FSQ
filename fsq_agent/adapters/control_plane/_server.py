# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import stat
import tempfile
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from threading import Thread
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from fsq_agent import application
from fsq_agent.application import ApplicationError, CaseSaveRequest, save_recorded_case
from fsq_agent.application.contracts import runs as contracts
from fsq_agent.case_dsl import FSQ_CASE_SUFFIX
from fsq_agent.models import ConfigurationError, FsqAgentError

from ._cases import discover_cases, resolve_case
from ._config import (
    ConfigAPIError,
    get_config,
    list_google_gemini_config_models,
    list_openai_config_models,
    map_config_exception,
    require_config_access,
    require_same_origin_write,
    save_azure_config,
    save_google_gemini_config,
    save_openai_config,
    test_saved_connection,
)
from ._directory_picker import DirectoryPicker, DirectoryPickerAPIError
from ._evidence import EvidenceProjection, read_replay_frames, read_screenshot, read_step_artifacts, read_ui_snapshot, safe_exception_message, safe_text
from ._execution import ExecutionHandle, prepare_run, start_execution
from ._provider_auth import ProviderAuthState
from ._readiness import AndroidPreflightError, MacOSPreflightError, load_control_plane_settings, readiness
from ._replay import read_replay_video, replay_video_metadata, store_replay_video
from ._state import BusyError, ControlPlaneState, RequestNotFoundError
from ._targets import discover_targets
from ._workspace_files import WorkspaceFileAPIError, list_workspace_entries, read_workspace_file
from ._workspaces import (
    WorkspaceAPIError,
    add_workspace_platform_request,
    create_workspace_request,
    get_workspace,
    get_workspace_platform,
    list_workspaces,
    map_workspace_exception,
    update_workspace_platform_request,
)

_HISTORY_PREFIX = "/api/control-plane/history"
_HISTORY_HEADERS = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True)
class _HistoryFile:
    path: Path
    size: int
    sha256: str | None = None


def _open_history_file(path: Path):
    path = path.absolute()
    if os.open in os.supports_dir_fd and hasattr(os, "O_NOFOLLOW"):
        descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for part in path.parts[1:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return os.fdopen(os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor), "rb")
        finally:
            os.close(descriptor)
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("History file path changed.")
    return path.open("rb")


def _copy_verified_history_file(body: _HistoryFile, target) -> None:
    if not body.sha256 or body.size < 0 or body.size > 512 * 1024 * 1024:
        raise ValueError("History file identity is unavailable.")
    digest = hashlib.sha256()
    with _open_history_file(body.path) as source:
        observed = os.fstat(source.fileno())
        if not stat.S_ISREG(observed.st_mode) or observed.st_size != body.size:
            raise ValueError("History file identity changed.")
        remaining = body.size
        while remaining:
            content = source.read(min(65536, remaining))
            if not content:
                raise ValueError("History file changed while reading.")
            digest.update(content)
            target.write(content)
            remaining -= len(content)
        if source.read(1) or digest.hexdigest() != body.sha256:
            raise ValueError("History file integrity changed.")
    target.seek(0)


def _history_one(query: dict[str, list[str]], name: str, *, required: bool = False) -> str | None:
    values = query.get(name, [])
    if len(values) > 1 or (values and not values[0].strip()) or (required and not values):
        raise ValueError(f"A single non-empty {name} is required.")
    return values[0] if values else None


def _history_scope(workspace: str | None, user_root: Path | None) -> dict[str, Any]:
    return {"workspace_name": workspace, "user_config_root": user_root}


def _history_route(path: str) -> tuple[str, str, tuple[str, ...]]:
    parts = tuple(unquote(part) for part in path.removeprefix(_HISTORY_PREFIX + "/").split("/"))
    if len(parts) < 2 or parts[0] not in {"web", "android", "windows", "macos"} or any(not part or part in {".", ".."} or "/" in part or "\\" in part for part in parts):
        raise ValueError("Invalid historical Run address.")
    return parts[0], parts[1], parts[2:]


def _list_history(query: dict[str, list[str]], user_root: Path | None) -> dict[str, Any]:
    if set(query) - {"workspace", "platform", "status", "mode", "case", "since", "limit"}:
        raise ValueError("Unsupported Run filter.")
    request = contracts.ListRunsRequest(
        **_history_scope(_history_one(query, "workspace", required=True), user_root),
        platform=_history_one(query, "platform"),
        statuses=tuple(query.get("status", [])),
        mode=_history_one(query, "mode"),
        case_id=_history_one(query, "case"),
        since=_history_one(query, "since"),
        limit=int(_history_one(query, "limit") or 20),
    )
    return application.list_runs(request).model_dump(mode="json")


def _get_history(platform: str, run_id: str, query: dict[str, list[str]], user_root: Path | None) -> dict[str, Any]:
    if set(query) - {"workspace", "baselineRunId", "relatedRunId"}:
        raise ValueError("Unsupported report query.")
    request = contracts.GetRunReportRequest(
        **_history_scope(_history_one(query, "workspace", required=True), user_root),
        platform=platform,
        run_id=run_id,
        baseline_run_id=_history_one(query, "baselineRunId"),
        related_run_ids=tuple(query.get("relatedRunId", [])),
    )
    return application.get_run_report(request).model_dump(mode="json")


def _export_history(platform: str, run_id: str, body: dict[str, Any], user_root: Path | None) -> dict[str, Any]:
    if set(body) - {"workspaceName", "format", "baselineRunId", "relatedRunIds"} or not isinstance(body.get("workspaceName"), str) or not body["workspaceName"].strip() or "format" not in body:
        raise ValueError("Choose a Workspace and one report format.")
    request = contracts.ExportRunReportRequest(
        **_history_scope(body["workspaceName"], user_root),
        platform=platform,
        run_id=run_id,
        format=body["format"],
        baseline_run_id=body.get("baselineRunId"),
        related_run_ids=body.get("relatedRunIds", ()),
    )
    result = application.export_run_report(request).model_dump(mode="json")
    result.pop("output_path", None)
    base = f"{_HISTORY_PREFIX}/{quote(platform, safe='')}/{quote(run_id, safe='')}/exports/{quote(str(result['export_id']), safe='')}/files"
    result["files"] = [
        {
            **{key: item[key] for key in ("file_id", "name", "mime_type", "size_bytes", "sha256") if key in item},
            "download_url": f"{base}/{quote(item['file_id'], safe='')}?workspace={quote(body['workspaceName'], safe='')}",
        }
        for item in result.get("files", [])
    ]
    return result


def _resolve_history_file(platform: str, run_id: str, suffix: tuple[str, ...], query: dict[str, list[str]], user_root: Path | None):
    if set(query) != {"workspace"}:
        raise ValueError("Only a Workspace scope is accepted.")
    if len(suffix) == 2 and suffix[0] == "artifacts":
        reference = {"kind": "source", "artifact_id": suffix[1]}
    elif len(suffix) == 4 and suffix[0] == "exports" and suffix[2] == "files":
        reference = {"kind": "export", "export_id": suffix[1], "file_id": suffix[3]}
    else:
        raise ValueError("Invalid report file address.")
    request = contracts.ResolveRunArtifactRequest(**_history_scope(_history_one(query, "workspace", required=True), user_root), platform=platform, run_id=run_id, reference=reference)
    return application.resolve_run_artifact(request)


def _history_file_response(resolved) -> tuple[_HistoryFile, dict[str, str]]:
    filename = getattr(resolved, "filename", resolved.path.name)
    disposition = "inline" if resolved.mime_type in {"image/png", "image/jpeg", "image/webp"} else "attachment"
    headers = {**_HISTORY_HEADERS, "Content-Type": resolved.mime_type, "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(filename, safe='')}"}
    return _HistoryFile(resolved.path, resolved.size, getattr(resolved, "sha256", None)), headers


_API_PREFIX = "/api/control-plane"
_CASE_SOURCE_LIMIT_BYTES = 512 * 1024
_JSON_HEADERS = {"Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store"}
_MAX_BODY_BYTES = 36 * 1024 * 1024
_FSQ_CASE_SUFFIX_LOWER = FSQ_CASE_SUFFIX.casefold()
_SAVE_CASE_FORBIDDEN = re.compile(r"[<>\"|\\/:*?\[\]\x00-\x1f\x7f-\x9f]")


class _RunNotTerminalError(RuntimeError):
    pass


@dataclass(frozen=True)
class ControlPlaneServerOptions:
    host: str = "127.0.0.1"
    port: int = 8879
    open_browser: bool = True
    static_path: Path | None = None
    user_config_root: Path | None = None


class ControlPlaneServer:
    def __init__(self, options: ControlPlaneServerOptions | None = None) -> None:
        self.options = options or ControlPlaneServerOptions()
        self.state = ControlPlaneState()
        self._static_root = (self.options.static_path or Path(__file__).parent / "static").resolve()
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None
        self._handles: dict[str, ExecutionHandle] = {}
        self._provider_auth = ProviderAuthState(self.options.user_config_root)
        self._directory_picker = DirectoryPicker()

    @property
    def port(self) -> int:
        return int(self._httpd.server_address[1]) if self._httpd is not None else self.options.port

    @property
    def url(self) -> str:
        return f"http://{self.options.host}:{self.port}"

    def start(self) -> None:
        if self._httpd is not None:
            return
        if self._entry_path() is None:
            raise FileNotFoundError(f"Control Plane frontend build not found under {self._static_root}. Run npm ci and npm run build.")
        self._httpd = _ControlPlaneHTTPServer((self.options.host, self.options.port), _RequestHandler, self)
        self._thread = Thread(target=self._httpd.serve_forever, name="fsq-control-plane-server", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._directory_picker.shutdown()
        self._provider_auth.shutdown()
        httpd = self._httpd
        self._httpd = None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None

    def handle_get(self, path: str, query: dict[str, list[str]] | None = None, *, peer_host: str | None = "127.0.0.1") -> tuple[int, Any, dict[str, str]]:
        query = query or {}
        try:
            if path == _HISTORY_PREFIX or path.startswith(_HISTORY_PREFIX + "/"):
                self._require_config_access(peer_host)
                if path == _HISTORY_PREFIX:
                    return 200, _list_history(query, self.options.user_config_root), dict(_HISTORY_HEADERS)
                platform, run_id, suffix = _history_route(path)
                if not suffix:
                    return 200, _get_history(platform, run_id, query, self.options.user_config_root), dict(_HISTORY_HEADERS)
                resolved = _resolve_history_file(platform, run_id, suffix, query, self.options.user_config_root)
                body, headers = _history_file_response(resolved)
                return 200, body, headers
            if path == f"{_API_PREFIX}/config":
                self._require_config_access(peer_host)
                return 200, get_config(self.options.user_config_root), dict(_JSON_HEADERS)
            if path == f"{_API_PREFIX}/workspaces":
                self._require_config_access(peer_host)
                return 200, list_workspaces(self.options.user_config_root), dict(_JSON_HEADERS)
            workspace_name, workspace_suffix = _workspace_route_or_none(path)
            if workspace_name is not None:
                self._require_config_access(peer_host)
                if workspace_suffix == "":
                    return 200, get_workspace(workspace_name, self.options.user_config_root), dict(_JSON_HEADERS)
                workspace_platform = _workspace_platform_suffix_or_none(workspace_suffix)
                if workspace_platform is not None:
                    return (
                        200,
                        get_workspace_platform(workspace_name, workspace_platform, self.options.user_config_root),
                        dict(_JSON_HEADERS),
                    )
                if workspace_suffix == "/entries":
                    return (
                        200,
                        list_workspace_entries(
                            workspace_name,
                            _workspace_path_query(query, required=False),
                            self.options.user_config_root,
                        ),
                        dict(_JSON_HEADERS),
                    )
                if workspace_suffix == "/file":
                    return (
                        200,
                        read_workspace_file(
                            workspace_name,
                            _workspace_path_query(query, required=True),
                            self.options.user_config_root,
                        ),
                        dict(_JSON_HEADERS),
                    )
                return 404, _error("not_found", "Control Plane workspace endpoint not found.", "Check the API path."), dict(_JSON_HEADERS)
            auth_request_id = _device_flow_route_or_none(path)
            if auth_request_id is not None:
                self._require_config_access(peer_host)
                return 200, self._provider_auth.get(auth_request_id), dict(_JSON_HEADERS)
            if path == f"{_API_PREFIX}/bootstrap":
                return 200, self.state.bootstrap(), dict(_JSON_HEADERS)
            if path == f"{_API_PREFIX}/readiness":
                workspace_name, platform = _workspace_platform_query(query)
                return 200, self._readiness(workspace_name, platform), dict(_JSON_HEADERS)
            if path == f"{_API_PREFIX}/targets":
                workspace_name, platform = _workspace_platform_query(query)
                settings = (
                    load_control_plane_settings(workspace_name, platform, self.options.user_config_root, diagnostic=True) if platform == "macos" else self._load_settings(workspace_name, platform)
                )
                return 200, discover_targets(settings), dict(_JSON_HEADERS)
            if path == f"{_API_PREFIX}/cases":
                workspace_name, platform = _workspace_platform_query(query)
                settings = (
                    load_control_plane_settings(workspace_name, platform, self.options.user_config_root, diagnostic=True) if platform == "macos" else self._load_settings(workspace_name, platform)
                )
                return 200, discover_cases(settings), dict(_JSON_HEADERS)
            request_id, suffix = _run_route(path)
            if suffix == "":
                if self.state.snapshot(request_id).get("terminal"):
                    self._hydrate_evidence(request_id)
                return 200, self.state.snapshot(request_id), dict(_JSON_HEADERS)
            if suffix == "/screen":
                self._hydrate_evidence(request_id)
                artifact, _ = self.state.artifact(request_id, "screenshot")
                data, headers = read_screenshot(artifact)
                frozen_platform = str(self.state.snapshot(request_id)["platform"])
                return 200, data, {**headers, "X-Evidence-Platform": frozen_platform, "Cache-Control": "no-store"}
            if suffix == "/ui-snapshot":
                self._hydrate_evidence(request_id)
                artifact, _ = self.state.artifact(request_id, "ui_snapshot")
                payload, headers = read_ui_snapshot(artifact)
                return 200, payload, {**_JSON_HEADERS, **headers}
            if suffix.startswith("/step-artifacts/"):
                run_dir = self._terminal_run_dir(request_id)
                step_id = unquote(suffix.removeprefix("/step-artifacts/")).strip()
                payload = read_step_artifacts(run_dir, step_id)
                snapshot = self.state.snapshot(request_id)
                try:
                    report = _get_history(snapshot["platform"], snapshot["runId"], {"workspace": [snapshot["workspaceName"]]}, self.options.user_config_root)["report"]
                    payload["comparison"] = next((item for item in report.get("comparison", {}).get("before_after", []) if item.get("step_execution_id") == step_id), None)
                    normalized = {item.get("artifact_id"): item for item in report.get("artifacts", []) if item.get("step_execution_id") == step_id}
                    for item in payload["artifacts"]:
                        projected = normalized.get(item.get("artifactId"), {})
                        if not projected or projected.get("availability") != "available" or projected.get("display_availability") == "omitted":
                            item.pop("content", None)
                            item.pop("contentBase64", None)
                            item.update(availability=projected.get("availability", "unavailable"), error=projected.get("unavailable_reason") or "Validated evidence is unavailable.")
                        elif item.get("kind") == "ui_snapshot":
                            item.pop("content", None)
                            if isinstance(projected.get("content"), str):
                                item["content"] = projected["content"]
                            else:
                                item.update(availability="unavailable", error=projected.get("unavailable_reason") or "Validated snapshot content is unavailable.")
                        if isinstance(projected.get("normalized_content"), str):
                            item["normalizedContent"] = projected["normalized_content"]
                        item["truncated"] = bool(item.get("truncated") or projected.get("truncated"))
                        for source, target in (("redacted", "redacted"), ("transformed", "transformed"), ("display_transformed", "displayTransformed")):
                            item[target] = bool(item.get(target) or projected.get(source))
                        for source, target in (
                            ("coverage", "coverage"),
                            ("compaction", "compaction"),
                            ("capture_occurrence", "captureOccurrence"),
                            ("capture_reason", "captureReason"),
                            ("attempt_index", "attemptIndex"),
                            ("step_execution_id", "stepExecutionId"),
                        ):
                            if projected.get(source) is not None:
                                item[target] = projected[source]
                except (ApplicationError, ValueError, OSError):
                    payload["comparison"] = {"status": "unavailable", "reason": "Persisted snapshot comparison is unavailable.", "rows": []}
                return 200, payload, dict(_JSON_HEADERS)
            if suffix == "/replay":
                return 200, read_replay_frames(self._terminal_run_dir(request_id)), dict(_JSON_HEADERS)
            if suffix == "/replay-video":
                video_url = f"{_API_PREFIX}/runs/{request_id}/replay-video/file"
                return 200, replay_video_metadata(self._terminal_run_dir(request_id), video_url), dict(_JSON_HEADERS)
            if suffix == "/stream":
                return 400, _error("sse_required", "Use an SSE client for the stream endpoint.", "Connect with EventSource."), dict(_JSON_HEADERS)
            return 404, _error("not_found", "Control Plane endpoint not found.", "Check the API path."), dict(_JSON_HEADERS)
        except ConfigAPIError as exc:
            return exc.status, _error(exc.code, exc.message, exc.action), dict(_JSON_HEADERS)
        except (WorkspaceAPIError, WorkspaceFileAPIError) as exc:
            return exc.status, _error(exc.code, exc.message, exc.action), dict(_JSON_HEADERS)
        except RequestNotFoundError:
            return 404, _error("request_not_found", "Run request not found.", "Reload Control Plane to find the active request."), dict(_JSON_HEADERS)
        except FileNotFoundError as exc:
            return 404, _error("evidence_unavailable", str(exc), "Wait for evidence capture or select another evidence view."), dict(_JSON_HEADERS)
        except OverflowError as exc:
            return 413, _error("evidence_too_large", str(exc), "Inspect the persisted artifact outside the Control Plane display."), dict(_JSON_HEADERS)
        except _RunNotTerminalError as exc:
            return 409, _exception_error("run_not_terminal", exc, "Wait for the run to finish."), dict(_JSON_HEADERS)
        except ApplicationError as exc:
            return 400, _error(exc.code.value, exc.message, exc.action or "Repair configuration and recheck.", details=exc.details), dict(_JSON_HEADERS)
        except (ValueError, FsqAgentError) as exc:
            return 400, _exception_error("invalid_request", exc, "Correct the request and retry."), dict(_JSON_HEADERS)
        except OSError as exc:
            return 503, _exception_error("unavailable", exc, "Verify local platform configuration and retry."), dict(_JSON_HEADERS)
        except Exception as exc:  # noqa: BLE001 - HTTP boundary does not expose tracebacks.
            return 500, _exception_error("internal_error", exc, "Retry or inspect the local server logs.", unexpected=True), dict(_JSON_HEADERS)

    def handle_post(
        self,
        path: str,
        body: dict[str, Any],
        *,
        peer_host: str | None = "127.0.0.1",
        origin: str | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        if path.startswith(_HISTORY_PREFIX + "/"):
            try:
                self._require_config_access(peer_host)
                require_same_origin_write(origin, host)
                platform, run_id, suffix = _history_route(path)
                if suffix != ("exports",):
                    return 404, _error("not_found", "Report export endpoint not found.", "Check the API path.")
                return 201, _export_history(platform, run_id, body, self.options.user_config_root)
            except ConfigAPIError as exc:
                return exc.status, _error(exc.code, exc.message, exc.action)
            except ApplicationError as exc:
                return 400, _error(exc.code.value, exc.message, exc.action or "Inspect the Run and retry.", details=exc.details)
            except (TypeError, ValueError):
                return 400, _error("invalid_export", "Invalid report export request.", "Select a Workspace and one supported format.")
            except Exception as exc:  # noqa: BLE001 - transport boundary exposes only safe errors.
                return 500, _exception_error("export_failed", exc, "Inspect the Run and retry.", unexpected=True)
        if path == f"{_API_PREFIX}/readiness":
            try:
                self._require_config_access(peer_host)
                require_same_origin_write(origin, host)
                if set(body) != {"workspaceName", "platform", "targetId"} or body.get("platform") != "android":
                    return 400, _error("invalid_diagnosis", "Invalid Android diagnosis fields.", "Select a Workspace and device.")
                return 200, readiness(body["workspaceName"], "android", self.options.user_config_root, target_id=body["targetId"])
            except ConfigAPIError as exc:
                return exc.status, _error(exc.code, exc.message, exc.action)
            except Exception:  # noqa: BLE001 -- diagnosis boundary never returns private inputs.
                return 400, _error("android_diagnosis_failed", "Android diagnosis could not be completed.", "Select a valid Workspace and device, then recheck.")
        if path == f"{_API_PREFIX}/workspaces/pick-parent-directory":
            return self._handle_workspace_write("POST", path, body, peer_host=peer_host, origin=origin, host=host)
        workspace_name, workspace_suffix = _workspace_route_or_none(path)
        if path == f"{_API_PREFIX}/workspaces" or (workspace_name is not None and workspace_suffix == "/platforms"):
            return self._handle_workspace_write("POST", path, body, peer_host=peer_host, origin=origin, host=host)
        if path.startswith(f"{_API_PREFIX}/config/"):
            return self._handle_config_write("POST", path, body, peer_host=peer_host, origin=origin, host=host)
        if path == f"{_API_PREFIX}/runs":
            return self._start_run(body)
        try:
            request_id, suffix = _run_route(path)
            if suffix == "/replay-video":
                stored = store_replay_video(self._terminal_run_dir(request_id), body.get("mimeType"), body.get("videoBase64"))
                return 200, {**stored, "videoUrl": f"{_API_PREFIX}/runs/{request_id}/replay-video/file"}
            if suffix == "/save-yaml":
                return 200, self._save_run_yaml(request_id, body)
            if suffix != "/cancel":
                return 404, _error("not_found", "Control Plane endpoint not found.", "Check the API path.")
            snapshot = self.state.request_cancel(request_id)
            handle = self._handles.get(request_id)
            if handle is not None:
                handle.cancel()
        except RequestNotFoundError:
            return 404, _error("request_not_found", "Run request not found.", "Reload Control Plane to find the active request.")
        except FileExistsError as exc:
            return 409, _exception_error("case.publication_conflict", exc, "Choose another Case name and retry.")
        except ConfigurationError as exc:
            return 400, _exception_error("case.invalid", exc, "Inspect the generated Case and recording metadata, then retry.")
        except FileNotFoundError as exc:
            return 404, _exception_error("generated_yaml_unavailable", exc, "Run Explore again or inspect the run artifacts.")
        except ValueError as exc:
            return 400, _exception_error("invalid_request", exc, "Correct the request and retry.")
        except OverflowError as exc:
            return 413, _exception_error("body_too_large", exc, "Upload a smaller replay video.")
        except _RunNotTerminalError as exc:
            return 409, _exception_error("run_not_terminal", exc, "Wait for the run to finish.")
        except OSError as exc:
            return 503, _exception_error("save_yaml_failed", exc, "Check workspace file permissions and retry.")
        else:
            return 200, snapshot

    def _save_run_yaml(self, request_id: str, body: dict[str, Any]) -> dict[str, Any]:
        case_name = _save_case_name(body)
        snapshot = self.state.snapshot(request_id)
        if not snapshot.get("terminal"):
            raise _RunNotTerminalError("Save yaml is available after the run reaches a terminal state.")
        if snapshot.get("mode") != "explore":
            raise ValueError("Save yaml is available only for Explore runs.")
        run_id = snapshot.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise FileNotFoundError("Generated YAML is unavailable for this run.")
        run_dir = self._terminal_run_dir(request_id)
        recorded_case_path = (run_dir / f"recorded{FSQ_CASE_SUFFIX}").resolve()
        try:
            recorded_case_path.relative_to(run_dir.resolve())
        except ValueError as exc:
            raise ValueError("Generated YAML path escapes the run directory.") from exc
        if not recorded_case_path.is_file():
            raise FileNotFoundError("Generated recorded.fsq.yaml was not found for this run.")
        frozen_cases_dir = self.state.cases_directory(request_id)
        if not isinstance(frozen_cases_dir, Path):
            raise FileNotFoundError("Frozen cases directory is unavailable for this run.")
        cases_dir = frozen_cases_dir.resolve()
        destination = (cases_dir / f"{case_name}{FSQ_CASE_SUFFIX}").resolve()
        try:
            destination.relative_to(cases_dir)
        except ValueError as exc:
            raise ValueError("Saved YAML path escapes the configured cases directory.") from exc
        saved = save_recorded_case(CaseSaveRequest(candidate_path=recorded_case_path, destination_directory=cases_dir, platform=snapshot["platform"], case_name=case_name))
        if saved.outcome == "conflict":
            raise FileExistsError("A different Case already uses this name.")
        if saved.outcome == "failed" or saved.path is None:
            raise OSError("Unable to save Case.")
        return {
            "savedPath": destination.relative_to(cases_dir).as_posix(),
            "outcome": saved.outcome,
            "draft": saved.draft,
            "message": f"{'Draft: ' if saved.draft else ''}{'Already saved' if saved.outcome == 'unchanged' else 'Saved YAML'} to cases/{snapshot['platform']}/{destination.name}.",
        }

    def handle_replay_video_file(self, request_id: str, range_header: str | None) -> tuple[int, bytes, dict[str, str]]:
        return read_replay_video(self._terminal_run_dir(request_id), range_header)

    def handle_put(
        self,
        path: str,
        body: dict[str, Any],
        *,
        peer_host: str | None = "127.0.0.1",
        origin: str | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        workspace_name, workspace_suffix = _workspace_route_or_none(path)
        if workspace_name is not None and _workspace_platform_suffix_or_none(workspace_suffix) is not None:
            return self._handle_workspace_write("PUT", path, body, peer_host=peer_host, origin=origin, host=host)
        return self._handle_config_write("PUT", path, body, peer_host=peer_host, origin=origin, host=host)

    def handle_delete(
        self,
        path: str,
        *,
        peer_host: str | None = "127.0.0.1",
        origin: str | None = None,
        host: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        return self._handle_config_write("DELETE", path, {}, peer_host=peer_host, origin=origin, host=host)

    def _handle_config_write(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        *,
        peer_host: str | None,
        origin: str | None,
        host: str | None,
    ) -> tuple[int, dict[str, Any]]:
        try:
            self._require_config_access(peer_host)
            require_same_origin_write(origin, host)
            if method == "POST" and path == f"{_API_PREFIX}/config/openai/models":
                return 200, list_openai_config_models(body)
            if method == "POST" and path == f"{_API_PREFIX}/config/google-gemini/models":
                return 200, list_google_gemini_config_models(body)
            if method == "PUT" and path == f"{_API_PREFIX}/config/google-gemini":
                return 200, save_google_gemini_config(body, self.options.user_config_root)
            if method == "PUT" and path == f"{_API_PREFIX}/config/openai":
                return 200, save_openai_config(body, self.options.user_config_root)
            if method == "PUT" and path == f"{_API_PREFIX}/config/azure":
                return 200, save_azure_config(body, self.options.user_config_root)
            if method == "POST" and path == f"{_API_PREFIX}/config/github/device-flow":
                return 202, self._provider_auth.start(body)
            models_auth_request_id = _device_flow_models_route_or_none(path)
            if method == "POST" and models_auth_request_id is not None:
                return 202, self._provider_auth.retry_models(models_auth_request_id, body)
            if method == "POST" and path == f"{_API_PREFIX}/config/test-connection":
                return 200, test_saved_connection(body, self.options.user_config_root)
            auth_request_id = _device_flow_route_or_none(path)
            if method == "PUT" and auth_request_id is not None:
                self._provider_auth.save(auth_request_id, body)
                return 200, get_config(self.options.user_config_root)
            if method == "DELETE" and auth_request_id is not None:
                return 200, self._provider_auth.cancel(auth_request_id)
            return 404, _error("not_found", "Control Plane Config endpoint not found.", "Check the API path and method.")
        except Exception as exc:  # noqa: BLE001 - Config boundary maps safe errors.
            mapped = map_config_exception(exc)
            return mapped.status, _error(mapped.code, mapped.message, mapped.action)

    def _handle_workspace_write(
        self,
        method: str,
        path: str,
        body: dict[str, Any],
        *,
        peer_host: str | None,
        origin: str | None,
        host: str | None,
    ) -> tuple[int, dict[str, Any]]:
        try:
            self._require_config_access(peer_host)
            require_same_origin_write(origin, host)
            if method == "POST" and path == f"{_API_PREFIX}/workspaces/pick-parent-directory":
                if body:
                    return 400, _error(
                        "invalid_directory_picker_request",
                        "Folder selection does not accept request fields.",
                        "Send an empty JSON object and retry.",
                    )
                return 200, self._directory_picker.choose()
            if method == "POST" and path == f"{_API_PREFIX}/workspaces":
                return 201, create_workspace_request(body, self.options.user_config_root)
            workspace_name, workspace_suffix = _workspace_route_or_none(path)
            if method == "POST" and workspace_name is not None and workspace_suffix == "/platforms":
                return 201, add_workspace_platform_request(workspace_name, body, self.options.user_config_root)
            platform = _workspace_platform_suffix_or_none(workspace_suffix)
            if method == "PUT" and workspace_name is not None and platform is not None:
                return 200, update_workspace_platform_request(workspace_name, platform, body, self.options.user_config_root)
            return 404, _error("not_found", "Control Plane workspace endpoint not found.", "Check the API path and method.")
        except ConfigAPIError as exc:
            return exc.status, _error(exc.code, exc.message, exc.action)
        except DirectoryPickerAPIError as exc:
            return exc.status, _error(exc.code, exc.message, exc.action)
        except Exception as exc:  # noqa: BLE001 - Workspace boundary maps safe errors.
            mapped = map_workspace_exception(exc)
            return mapped.status, _error(mapped.code, mapped.message, mapped.action)

    def _require_config_access(self, peer_host: str | None) -> None:
        require_config_access(self.options.host, peer_host)

    def _load_settings(self, workspace_name: str, platform: str) -> Any:
        return (
            load_control_plane_settings(workspace_name, platform, self.options.user_config_root, diagnostic=True)
            if platform == "macos"
            else load_control_plane_settings(workspace_name, platform, self.options.user_config_root)
        )

    def _readiness(self, workspace_name: str, platform: str) -> dict[str, Any]:
        return readiness(workspace_name, platform, self.options.user_config_root)

    def sse_snapshots(self, request_id: str, *, after_sequence: int = 0, timeout: float = 15.0):
        revision = -1
        sequence = max(0, after_sequence)
        while True:
            snapshot, revision = self.state.wait_for_update(request_id, after_sequence=sequence, revision=revision, timeout=timeout)
            events = snapshot.get("events") or []
            if events:
                sequence = max(sequence, *(int(event.get("sequence", 0)) for event in events))
            yield snapshot
            if snapshot.get("terminal"):
                return

    def static_response(self, request_path: str) -> tuple[int, bytes, str]:
        decoded = unquote(urlsplit(request_path).path)
        relative = PurePosixPath(decoded.lstrip("/"))
        if any(part in {"..", "."} for part in relative.parts):
            return 404, b"Not found", "text/plain; charset=utf-8"
        candidate = (self._static_root / Path(*relative.parts)).resolve()
        if _is_relative_to(candidate, self._static_root) and candidate.is_file():
            return 200, candidate.read_bytes(), mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if relative.suffix:
            return 404, b"Not found", "text/plain; charset=utf-8"
        entry = self._entry_path()
        if entry is None:
            return 404, b"Control Plane frontend build not found. Run npm ci and npm run build.", "text/plain; charset=utf-8"
        return 200, entry.read_bytes(), "text/html; charset=utf-8"

    def _start_run(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        workspace_name = body.get("workspaceName")
        platform = body.get("platform")
        target_id = body.get("targetId")
        mode = body.get("mode")
        if not isinstance(workspace_name, str) or not workspace_name.strip() or not isinstance(platform, str) or not isinstance(target_id, str) or not isinstance(mode, str):
            return 400, _error("invalid_run", "workspaceName, mode, platform, and targetId are required.", "Complete the run form and retry.")
        workspace_name = workspace_name.strip()
        source = {"goal": body["goal"]} if isinstance(body.get("goal"), str) else {"casePath": body["casePath"]} if isinstance(body.get("casePath"), str) else {}
        settings = None
        try:
            request_id = self.state.reserve(workspace_name=workspace_name, platform=platform, target_id=target_id, mode=mode, source=source)
        except BusyError as exc:
            return 409, _exception_error("busy", exc, "Wait for the active run to finish or cancel it.")
        try:
            settings = self._load_settings(workspace_name, platform)
            self.state.bind_cases_dir(request_id, Path(settings.cases.dir).resolve())
            source = self._run_source(body, settings)
            if source:
                self.state.update_source(request_id, source)
            prepared = prepare_run(request_id=request_id, settings=settings, body=body)
            if getattr(prepared, "mode", None) == "strict":
                self.state.update_source(request_id, {"caseSteps": _strict_case_steps(prepared)})
            self._handles[request_id] = start_execution(prepared, self.state)
        except AndroidPreflightError as exc:
            self.state.abandon_preparation(request_id)
            return 400, {"code": "android_preflight_failed", "message": exc.message, "action": exc.action, "details": exc.details}
        except MacOSPreflightError as exc:
            self.state.abandon_preparation(request_id)
            return 400, {"code": "macos_preflight_failed", "message": exc.message, "action": exc.action, "details": exc.details}
        except (TypeError, ValueError, FsqAgentError, OSError, UnicodeDecodeError) as exc:
            self.state.abandon_preparation(request_id)
            return 400, _exception_error("run_validation_failed", exc, "Refresh readiness, targets, and cases, then retry.", settings=settings)
        except Exception as exc:  # noqa: BLE001
            self.state.abandon_preparation(request_id)
            return 500, _exception_error("run_start_failed", exc, "Inspect local configuration and retry.", unexpected=True)
        else:
            return 202, {"requestId": request_id}

    def _run_source(self, body: dict[str, Any], settings) -> dict[str, Any]:
        if isinstance(body.get("goal"), str):
            return {"goal": body["goal"]}
        if not isinstance(body.get("casePath"), str):
            return {}
        case_path = resolve_case(settings, body["casePath"])
        content_bytes = case_path.read_bytes()
        if len(content_bytes) > _CASE_SOURCE_LIMIT_BYTES:
            raise ValueError(f"Case YAML is too large to display ({len(content_bytes)} bytes).")
        return {"casePath": body["casePath"], "caseContent": content_bytes.decode("utf-8")}

    def _hydrate_evidence(self, request_id: str) -> None:
        artifact, run_id = self.state.artifact(request_id, "screenshot")
        ui_artifact, _ = self.state.artifact(request_id, "ui_snapshot")
        if not run_id:
            return
        run_dir = self.state.run_directory(request_id)
        if isinstance(run_dir, Path):
            projection = EvidenceProjection(self.state, request_id, run_dir.parent)
        else:
            snapshot = self.state.snapshot(request_id)
            settings = self._load_settings(str(snapshot["workspaceName"]), str(snapshot["platform"]))
            projection = EvidenceProjection(self.state, request_id, Path(settings.output.runs_dir))
        projection.bind_run(run_id)
        if not (artifact and ui_artifact):
            projection.load_persisted_manifest()
        projection.load_persisted_step_ids()

    def _terminal_run_dir(self, request_id: str) -> Path:
        snapshot = self.state.snapshot(request_id)
        if not snapshot.get("terminal"):
            raise _RunNotTerminalError("Run evidence is available after the run reaches a terminal state.")
        run_id = snapshot.get("runId")
        if not isinstance(run_id, str) or not run_id:
            raise FileNotFoundError("Run artifacts are unavailable.")
        run_dir = self.state.run_directory(request_id)
        if not isinstance(run_dir, Path) or not run_dir.is_dir():
            raise FileNotFoundError("Run artifacts are unavailable.")
        return run_dir

    def _entry_path(self) -> Path | None:
        candidates = (self._static_root / "control-plane" / "index.html", self._static_root / "index.html")
        return next((path for path in candidates if path.is_file()), None)


def _strict_case_steps(prepared) -> list[dict[str, Any]]:
    if prepared.case_path is None:
        return []
    steps = prepared.resolved_steps_by_path.get(prepared.case_path.resolve(), [])
    summaries: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        step_index = step.source_ref.step_index + 1 if step.source_ref is not None else index + 1
        summaries.append(
            {
                "stepId": step.step_id,
                "sourceStepId": step.step_id,
                "index": step_index,
                "authoredActionName": step.metadata.get("authored_action_name") or step.action_name,
                "actionName": step.action_name,
                "kind": step.kind,
            }
        )
    return summaries


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with (
            source.open("rb") as source_file,
            tempfile.NamedTemporaryFile(
                mode="wb",
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".tmp",
                delete=False,
            ) as temporary_file,
        ):
            temporary_path = Path(temporary_file.name)
            while chunk := source_file.read(1024 * 1024):
                temporary_file.write(chunk)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _save_case_name(body: dict[str, Any]) -> str:
    if set(body) != {"caseName"}:
        raise ValueError("Save yaml requires exactly caseName.")
    case_name = body.get("caseName")
    if not isinstance(case_name, str):
        raise TypeError("caseName must be a string.")
    case_name = case_name.strip()
    if not case_name:
        raise ValueError("caseName must be non-empty.")
    if case_name.casefold().endswith(_FSQ_CASE_SUFFIX_LOWER):
        raise ValueError("caseName must not include the .fsq.yaml suffix.")
    if case_name.startswith(".") or case_name in {".."}:
        raise ValueError("caseName must not start with a dot.")
    if _SAVE_CASE_FORBIDDEN.search(case_name) or ".." in case_name:
        raise ValueError("caseName must be a safe filename without path or wildcard characters.")
    return case_name


class _ControlPlaneHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler, control_plane: ControlPlaneServer) -> None:
        self.control_plane = control_plane
        super().__init__(address, handler)


class _RequestHandler(BaseHTTPRequestHandler):
    server: _ControlPlaneHTTPServer

    def do_GET(self) -> None:
        parsed = urlsplit(self.path)
        query = parse_qs(parsed.query, keep_blank_values=True)
        if parsed.path.startswith(_API_PREFIX):
            request_id, suffix = _run_route_or_empty(parsed.path)
            if request_id and suffix == "/stream":
                self._send_sse(request_id, query)
                return
            if request_id and suffix == "/replay-video/file":
                try:
                    status, body, headers = self.server.control_plane.handle_replay_video_file(request_id, self.headers.get("Range"))
                    self._send(status, body, headers)
                except RequestNotFoundError:
                    self._send(404, _error("request_not_found", "Run request not found.", "Reload Control Plane."), _JSON_HEADERS)
                except FileNotFoundError as exc:
                    self._send(404, _error("evidence_unavailable", str(exc), "Wait for replay generation."), _JSON_HEADERS)
                except (_RunNotTerminalError, ValueError) as exc:
                    self._send(409 if isinstance(exc, _RunNotTerminalError) else 416, _error("invalid_range", str(exc), "Retry with a valid range after completion."), _JSON_HEADERS)
                return
            status, body, headers = self.server.control_plane.handle_get(parsed.path, query, peer_host=self.client_address[0])
            self._send(status, body, headers)
            return
        status, body, content_type = self.server.control_plane.static_response(self.path)
        self._send(status, body, {"Content-Type": content_type})

    def do_POST(self) -> None:
        self._handle_json_write("POST")

    def do_PUT(self) -> None:
        self._handle_json_write("PUT")

    def do_DELETE(self) -> None:
        self._handle_json_write("DELETE")

    def _handle_json_write(self, method: str) -> None:
        parsed = urlsplit(self.path)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > _MAX_BODY_BYTES:
                self._send(413, _error("body_too_large", "Request body is too large.", "Send a smaller JSON request."), _JSON_HEADERS)
                return
            body = _decode_json_body(self.rfile.read(length))
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send(400, _error("invalid_json", "Request body must be a JSON object.", "Correct the request body."), _JSON_HEADERS)
            return
        request_kwargs = {
            "peer_host": self.client_address[0],
            "origin": self.headers.get("Origin"),
            "host": self.headers.get("Host"),
        }
        if method == "POST":
            status, payload = self.server.control_plane.handle_post(parsed.path, body, **request_kwargs)
        elif method == "PUT":
            status, payload = self.server.control_plane.handle_put(parsed.path, body, **request_kwargs)
        else:
            status, payload = self.server.control_plane.handle_delete(parsed.path, **request_kwargs)
        self._send(status, payload, _JSON_HEADERS)

    def log_message(self, format_string: str, *args: Any) -> None:
        return

    def handle_one_request(self) -> None:
        try:
            super().handle_one_request()
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _send_sse(self, request_id: str, query: dict[str, list[str]]) -> None:
        try:
            after = _after_sequence(query)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            for snapshot in self.server.control_plane.sse_snapshots(request_id, after_sequence=after):
                events = snapshot.get("events") or []
                sequence = max((int(event.get("sequence", 0)) for event in events), default=after)
                payload = json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(f"id: {sequence}\nevent: snapshot\ndata: {payload}\n\n".encode())
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return
        except (RequestNotFoundError, ValueError) as exc:
            if not self.wfile.closed:
                payload = json.dumps(_exception_error("stream_error", exc, "Reload the active run."), separators=(",", ":"))
                self.wfile.write(f"event: error\ndata: {payload}\n\n".encode())

    def _send(self, status: int, body: Any, headers: dict[str, str]) -> None:
        if isinstance(body, _HistoryFile):
            with tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024) as stream:
                try:
                    _copy_verified_history_file(body, stream)
                except (OSError, ValueError):
                    self._send(409, _error("artifact_changed", "Artifact changed or became unavailable before delivery.", "Reload the report and retry the download."), dict(_HISTORY_HEADERS))
                    return
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(body.size))
                self.end_headers()
                while chunk := stream.read(65536):
                    self.wfile.write(chunk)
            return
        if isinstance(body, bytes):
            encoded = body
        else:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        if urlsplit(self.path).path.startswith(_HISTORY_PREFIX):
            headers = {**headers, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def run_control_plane(options: ControlPlaneServerOptions) -> None:
    server = ControlPlaneServer(options)
    server.start()
    try:
        print(f"Control Plane: {server.url}")
        if options.open_browser:
            webbrowser.open(server.url)
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        return
    finally:
        server.stop()


def _platform_query(query: dict[str, list[str]]) -> str:
    values = query.get("platform") or []
    if len(values) != 1 or values[0] not in {"android", "web", "windows", "macos"}:
        raise ValueError("platform must be one of android, web, windows, or macos.")
    return values[0]


def _workspace_platform_query(query: dict[str, list[str]]) -> tuple[str, str]:
    workspace_values = query.get("workspace") or []
    if len(workspace_values) != 1 or not workspace_values[0].strip():
        raise ValueError("workspace must be one registered workspace name.")
    return workspace_values[0].strip(), _platform_query(query)


def _decode_json_body(raw: bytes) -> dict[str, Any]:
    body = json.loads(raw or b"{}")
    if not isinstance(body, dict):
        raise TypeError("Request body must be a JSON object.")
    return body


def _after_sequence(query: dict[str, list[str]]) -> int:
    values = query.get("afterSequence") or ["0"]
    if len(values) != 1:
        raise ValueError("afterSequence must be one non-negative integer.")
    value = int(values[0])
    if value < 0:
        raise ValueError("afterSequence must be non-negative.")
    return value


def _run_route(path: str) -> tuple[str, str]:
    prefix = f"{_API_PREFIX}/runs/"
    if not path.startswith(prefix):
        raise ValueError("Invalid run endpoint.")
    remainder = unquote(path.removeprefix(prefix))
    request_id, separator, suffix = remainder.partition("/")
    if not request_id:
        raise ValueError("request id is required.")
    return request_id, f"/{suffix}" if separator else ""


def _run_route_or_empty(path: str) -> tuple[str | None, str | None]:
    try:
        return _run_route(path)
    except ValueError:
        return None, None


def _device_flow_route_or_none(path: str) -> str | None:
    prefix = f"{_API_PREFIX}/config/github/device-flow/"
    if not path.startswith(prefix):
        return None
    auth_request_id = unquote(path.removeprefix(prefix))
    return auth_request_id if auth_request_id and "/" not in auth_request_id else None


def _device_flow_models_route_or_none(path: str) -> str | None:
    suffix = "/models"
    if not path.endswith(suffix):
        return None
    return _device_flow_route_or_none(path.removesuffix(suffix))


def _workspace_route_or_none(path: str) -> tuple[str | None, str | None]:
    prefix = f"{_API_PREFIX}/workspaces/"
    if not path.startswith(prefix):
        return None, None
    remainder = unquote(path.removeprefix(prefix))
    workspace_name, separator, suffix = remainder.partition("/")
    if not workspace_name:
        return None, None
    return workspace_name, f"/{suffix}" if separator else ""


def _workspace_path_query(query: dict[str, list[str]], *, required: bool) -> str:
    values = query.get("path")
    if not values:
        if not required:
            return ""
        raise WorkspaceFileAPIError(400, "invalid_workspace_path", "A workspace file path is required.", "Select a file and retry.")
    if len(values) != 1:
        raise WorkspaceFileAPIError(400, "invalid_workspace_path", "Workspace path must have one value.", "Select a workspace path and retry.")
    return values[0]


def _workspace_platform_suffix_or_none(suffix: str | None) -> str | None:
    prefix = "/platforms/"
    if suffix is None or not suffix.startswith(prefix):
        return None
    platform = suffix.removeprefix(prefix)
    return platform if platform and "/" not in platform else None


def _error(code: str, message: str, action: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"code": code, "message": safe_text(message), "action": safe_text(action, limit=500)}
    if details:
        payload["details"] = _safe_details(details)
    return payload


def _exception_error(code: str, exc: BaseException, action: str, *, settings: Any | None = None, unexpected: bool = False) -> dict[str, Any]:
    return _error(code, safe_exception_message(exc, settings=settings, unexpected=unexpected, limit=1000), action)


def _safe_details(value: Any, *, depth: int = 0) -> Any:
    if depth >= 4:
        return "[details omitted]"
    if isinstance(value, dict):
        return {safe_text(key, limit=100): _safe_details(item, depth=depth + 1) for key, item in list(value.items())[:50]}
    if isinstance(value, list):
        return [_safe_details(item, depth=depth + 1) for item in value[:50]]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return safe_text(value, limit=500)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


__all__ = ["ControlPlaneServer", "ControlPlaneServerOptions", "run_control_plane"]
