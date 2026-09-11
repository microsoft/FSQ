# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fsq_agent.agent_engine import EngineError
from fsq_agent.ai_services import CaseSuggestionAnalysis
from fsq_agent.application import ApplicationError, ApplicationErrorCode, CaseTestRequest
from fsq_agent.application import _case_test as case_test_module


@pytest.mark.parametrize("platform", ["android", "web", "windows", "macos"])
def test_case_test_request_supports_all_public_platforms(platform: str, tmp_path: Path) -> None:
    request = CaseTestRequest(current_directory=tmp_path, platform=platform, case_path=Path("case.fsq.yaml"))
    assert request.platform == platform


def test_case_test_rejects_missing_case_with_stable_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_test_module, "require_initialized_workspace", lambda _request: type("Workspace", (), {"workspace": tmp_path.resolve()})())
    settings = SimpleNamespace(cases=SimpleNamespace(dir=tmp_path / "cases"))
    monkeypatch.setattr(case_test_module, "load_workspace_platform_settings", lambda *_args: settings)

    with pytest.raises(ApplicationError) as error:
        case_test_module.execute_case_test(
            CaseTestRequest(current_directory=tmp_path, platform="web", case_path=Path("missing.fsq.yaml")),
            suggestion_analyzer_factory=None,
        )

    assert error.value.code == ApplicationErrorCode.CASE_NOT_FOUND


def test_case_test_rejects_invalid_case_with_stable_request_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_test_module, "require_initialized_workspace", lambda _request: type("Workspace", (), {"workspace": tmp_path.resolve()})())
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    invalid_case = cases_dir / "invalid.fsq.yaml"
    invalid_case.write_text("schemaVersion: unsupported/v1\nname: Invalid schema\nplatform: web\n", encoding="utf-8")
    settings = SimpleNamespace(cases=SimpleNamespace(dir=cases_dir))
    monkeypatch.setattr(case_test_module, "load_workspace_platform_settings", lambda *_args: settings)

    with pytest.raises(ApplicationError) as error:
        case_test_module.execute_case_test(
            CaseTestRequest(current_directory=tmp_path, platform="web", case_path=invalid_case),
            suggestion_analyzer_factory=None,
        )

    assert error.value.code == ApplicationErrorCode.CASE_INVALID
    assert error.value.category.value == "request_validation"
    assert error.value.details["field_path"] == ["schemaVersion"]
    assert "unsupported/v1" not in str(error.value.details)


def test_suggestion_artifacts_are_run_local_and_source_immutable(tmp_path: Path) -> None:
    source = tmp_path / "search.fsq.yaml"
    original = "schemaVersion: fsq.ai-test/v1\nname: Search\nplatform: web\n"
    source.write_text(original, encoding="utf-8")
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    candidate = "schemaVersion: fsq.ai-test/v1\nname: Improved search\nplatform: web\n"

    suggestion, candidate_path = case_test_module._write_analysis_artifacts(
        run_dir=run_dir,
        source_case=source,
        source_platform="web",
        execution_status="failed",
        execution_summary="Case failed with 1 failed step.",
        analysis=CaseSuggestionAnalysis(
            summary="Improve the target.",
            suggestions=({"kind": "replace_target", "message": "Use the semantic search field."},),
            candidate_case_yaml=candidate,
        ),
    )
    payload = json.loads(suggestion.read_text(encoding="utf-8"))

    assert payload["source_case_immutable"] is True
    assert payload["execution_status"] == "failed"
    assert payload["analysis_summary"] == "Improve the target."
    assert candidate_path == run_dir / "candidate.fsq.yaml"
    assert payload["candidate_case_status"] == "available"
    from fsq_agent.case_dsl import FsqCaseLoader

    assert FsqCaseLoader().load_case(candidate_path).config.name == "Search"
    assert source.read_text(encoding="utf-8") == original
    assert not (tmp_path / "candidate.fsq.yaml").exists()


@pytest.mark.parametrize(
    "candidate",
    [
        "schemaVersion: fsq.ai-test/v1\nname: Wrong\nplatform: android\n",
        "schemaVersion: fsq.ai-test/v1\nname: Invalid: unquoted colon\nplatform: web\n",
    ],
)
def test_suggestion_omits_invalid_candidate_but_preserves_valid_analysis(tmp_path: Path, candidate: str) -> None:
    source = tmp_path / "search.fsq.yaml"
    source.write_text("schemaVersion: fsq.ai-test/v1\nname: Search\nplatform: web\n", encoding="utf-8")
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)

    suggestion_path, candidate_path = case_test_module._write_analysis_artifacts(
        run_dir=run_dir,
        source_case=source,
        source_platform="web",
        execution_status="passed",
        execution_summary="Case passed.",
        analysis=CaseSuggestionAnalysis(
            summary="A useful analysis remains available.",
            suggestions=({"kind": "stability", "message": "Use a stable locator."},),
            candidate_case_yaml=candidate,
        ),
    )

    payload = json.loads(suggestion_path.read_text(encoding="utf-8"))
    assert candidate_path is None
    assert payload["suggestions"] == [{"kind": "stability", "message": "Use a stable locator."}]
    assert payload["candidate_case_path"] is None
    assert payload["candidate_case_status"] == "invalid"
    assert not (run_dir / "candidate.fsq.yaml").exists()


def test_deprecated_suffix_warning_is_machine_visible() -> None:
    from fsq_agent.application import CaseTestResult

    result = CaseTestResult(
        run_id="run-1",
        status="success",
        summary="passed",
        report_path=Path("report.md"),
        warnings=["case.suffix_deprecated: rename this Case to *.fsq.yaml"],
    )
    assert result.model_dump(mode="json")["warnings"][0].startswith("case.suffix_deprecated")


def test_registered_workspace_name_is_not_directory_basename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = SimpleNamespace(name="registered-shop", root_path=tmp_path)
    monkeypatch.setattr(case_test_module, "list_workspace_registry", lambda: [entry])
    assert case_test_module._registered_workspace_name(tmp_path) == "registered-shop"


def test_bounded_execution_facts_limit_items_strings_and_total_size() -> None:
    report = {
        "run_id": "run-1",
        "summary": {"status": "failed"},
        "steps": [{"message": "x" * 10_000} for _ in range(150)],
        "events": [{"message": "y" * 10_000} for _ in range(150)],
        "artifacts": [{"secret": "not included"}],
    }

    facts = case_test_module._bounded_execution_facts(report)

    assert facts == {"run_id": "run-1", "summary": {"status": "failed"}, "truncated": True}
    assert len(json.dumps(facts).encode()) <= case_test_module._MAX_FACT_BYTES


@pytest.mark.parametrize("execution_status", ["passed", "failed"])
@pytest.mark.parametrize("analysis_error", [TimeoutError("provider unavailable"), EngineError("invalid_output", "Invalid suggestion output")])
@pytest.mark.parametrize("processing_write_fails", [False, True])
def test_suggest_runs_case_once_then_analyzes_and_returns_no_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, execution_status: str, analysis_error: Exception, processing_write_fails: bool
) -> None:
    case_path = tmp_path / "search.fsq.yaml"
    source = "schemaVersion: fsq.ai-test/v1\nname: Search\nplatform: web\n"
    case_path.write_text(source, encoding="utf-8")
    run_dir = tmp_path / "runs" / "search-2026-01-01_00-00-00"
    run_dir.mkdir(parents=True)
    report_path = run_dir / "core-report.md"
    report_path.write_text("report", encoding="utf-8")
    report_path.with_suffix(".json").write_text(
        json.dumps({"run_id": "run-1", "summary": {"status": execution_status, "failed_steps": 0 if execution_status == "passed" else 1, "total_steps": 1}, "steps": [], "events": []}),
        encoding="utf-8",
    )
    settings = SimpleNamespace(
        cases=SimpleNamespace(dir=tmp_path),
        output=SimpleNamespace(runs_dir=tmp_path / "runs"),
        harness=SimpleNamespace(android=SimpleNamespace(app_id=None)),
        runtime_secrets=SimpleNamespace(private_values=dict),
        execution=SimpleNamespace(post_action_delay_seconds=0),
    )
    order: list[str] = []
    transitions: list[tuple[str, dict]] = []
    monkeypatch.setattr(case_test_module, "require_initialized_workspace", lambda _request: SimpleNamespace(workspace=tmp_path))
    monkeypatch.setattr(case_test_module, "list_workspace_registry", lambda: [SimpleNamespace(name="test-workspace", root_path=tmp_path)])
    monkeypatch.setattr(case_test_module, "load_workspace_platform_settings", lambda *_args: settings)

    class Registry:
        def snapshot(self):
            return object()

    monkeypatch.setattr(case_test_module, "build_capability_registry", lambda **_kwargs: Registry())
    monkeypatch.setattr(case_test_module, "collect_strict_lifecycle_cases", lambda **kwargs: [(case_path, kwargs["case"])])
    monkeypatch.setattr(case_test_module.FsqExecutableStepAdapter, "to_executable_steps", lambda *_args: [])
    monkeypatch.setattr(case_test_module, "steps_require_provider", lambda *_args: False)
    monkeypatch.setattr(case_test_module, "validate_strict_core_settings", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(case_test_module.HarnessFactory, "create_harness", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(case_test_module.RuntimeSecretStore, "from_settings", lambda *_args: object())

    def run_once(**_kwargs):
        order.append("execute")
        return SimpleNamespace(path=report_path, evidence_manifest_path=run_dir / "manifest.json")

    monkeypatch.setattr(case_test_module, "run_strict_lifecycle_case", run_once)
    monkeypatch.setattr(
        case_test_module,
        "allocate_run",
        lambda **_kwargs: SimpleNamespace(run_id=run_dir.name, status="preparing"),
    )

    def transition(_run_dir, metadata, status, **updates):
        transitions.append((status, updates))
        return SimpleNamespace(run_id=metadata.run_id, status=status)

    monkeypatch.setattr(case_test_module, "transition_run", transition)

    class Analyzer:
        def analyze(self, *, parsed_case, execution_report):
            order.append("analyze")
            assert parsed_case["config"]["platform"] == "web"
            assert execution_report["summary"]["status"] == execution_status
            return CaseSuggestionAnalysis(summary="No change needed.", suggestions=())

    result = case_test_module.execute_case_test(
        CaseTestRequest(current_directory=tmp_path, platform="web", case_path=case_path, suggest=True),
        suggestion_analyzer_factory=lambda _settings: Analyzer(),
    )

    assert order == ["execute", "analyze"]
    expected_status = "success" if execution_status == "passed" else "failed"
    assert result.status == expected_status
    assert result.suggestion_path == run_dir / "case-suggestions.json"
    assert result.candidate_case_path is None
    payload = json.loads(result.suggestion_path.read_text(encoding="utf-8"))
    assert payload["candidate_case_status"] == "absent"
    assert case_path.read_text(encoding="utf-8") == source

    if processing_write_fails:

        def fail_processing_write(path, content):
            raise OSError("secondary processing write failure")

        monkeypatch.setattr(case_test_module, "_atomic_write", fail_processing_write)

    class FailingAnalyzer:
        def analyze(self, *, parsed_case, execution_report):
            raise analysis_error

    completed = case_test_module.execute_case_test(
        CaseTestRequest(current_directory=tmp_path, platform="web", case_path=case_path, suggest=True),
        suggestion_analyzer_factory=lambda _settings: FailingAnalyzer(),
    )
    assert completed.status == expected_status
    assert completed.processing["suggestion"]["status"] == "failed"
    assert completed.report_path == report_path
    assert any("case.suggestion_failed" in warning for warning in completed.warnings)
    assert report_path.read_text(encoding="utf-8") == "report"
    assert case_path.read_text(encoding="utf-8") == source
    assert len(list(run_dir.glob("*.fsq.yaml"))) == 0
    assert order == ["execute", "analyze", "execute"]
    assert [status for status, _ in transitions] == ["running", "running"]
    if processing_write_fails:
        assert completed.processing["suggestion"]["persistence"] == "unavailable"


def test_suggestion_cannot_introduce_run_metadata(tmp_path: Path) -> None:
    from fsq_agent.case_dsl import FsqCaseLoader

    source = tmp_path / "source.fsq.yaml"
    source.write_text("schemaVersion: fsq.ai-test/v1\nname: stable\nplatform: web\nproperties: {owner: team}\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, candidate = case_test_module._write_analysis_artifacts(
        run_dir=run_dir,
        source_case=source,
        source_platform="web",
        execution_status="passed",
        execution_summary="ok",
        analysis=CaseSuggestionAnalysis(
            summary="suggestion",
            suggestions=(),
            candidate_case_yaml="schemaVersion: fsq.ai-test/v1\nname: new\nplatform: web\nproperties: {recording: {source_run_id: changing-run}}\n---\n- startBrowser: {}\n",
        ),
    )
    assert candidate is not None
    assert FsqCaseLoader().load_case(candidate).config.properties == {"owner": "team"}
    assert "changing-run" not in candidate.read_text()


def test_suggestion_uses_executed_metadata_after_external_edit(tmp_path: Path) -> None:
    from fsq_agent.case_dsl import FsqCaseLoader

    source = tmp_path / "source.fsq.yaml"
    source.write_text("schemaVersion: fsq.ai-test/v1\nname: executed\nplatform: web\n")
    parsed = FsqCaseLoader().load_case(source)
    source.write_text("schemaVersion: fsq.ai-test/v1\nname: externally-edited\nplatform: web\n")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _, candidate = case_test_module._write_analysis_artifacts(
        run_dir=run_dir,
        source_case=source,
        parsed_source=parsed,
        source_platform="web",
        execution_status="passed",
        execution_summary="ok",
        analysis=CaseSuggestionAnalysis(summary="suggestion", suggestions=(), candidate_case_yaml=source.read_text()),
    )
    assert FsqCaseLoader().load_case(candidate).config.name == "executed"
    assert FsqCaseLoader().load_case(source).config.name == "externally-edited"


def test_suggestion_status_disk_failure_preserves_completed_result(tmp_path, monkeypatch):
    from fsq_agent.application import _case_test
    from fsq_agent.execution import RunResultSummary
    from fsq_agent.models import ReportArtifact

    source = tmp_path / "case.fsq.yaml"
    source.write_text("schemaVersion: fsq.ai-test/v1\nname: demo\nplatform: web\n")
    settings = SimpleNamespace(
        cases=SimpleNamespace(dir=tmp_path),
        output=SimpleNamespace(runs_dir=tmp_path),
        harness=SimpleNamespace(android=SimpleNamespace(app_id=None)),
        runtime_secrets=SimpleNamespace(private_values=dict),
        execution=SimpleNamespace(post_action_delay_seconds=0),
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text("{}")
    (run_dir / "execution-result.json").write_text('{"outcome":"success"}')
    metadata = SimpleNamespace(run_id="run")
    monkeypatch.setattr(_case_test, "require_initialized_workspace", lambda request: SimpleNamespace(workspace=tmp_path))
    monkeypatch.setattr(_case_test, "load_workspace_platform_settings", lambda *args: settings)
    monkeypatch.setattr(_case_test, "_registered_workspace_name", lambda root: "demo")
    monkeypatch.setattr(_case_test, "build_capability_registry", lambda **kwargs: SimpleNamespace(snapshot=lambda: None))
    monkeypatch.setattr(_case_test, "collect_strict_lifecycle_cases", lambda **kwargs: [])
    monkeypatch.setattr(_case_test, "validate_strict_core_settings", lambda *args, **kwargs: None)
    monkeypatch.setattr(_case_test, "allocate_run", lambda **kwargs: metadata)
    monkeypatch.setattr(_case_test, "transition_run", lambda *args, **kwargs: metadata)
    monkeypatch.setattr(_case_test.HarnessFactory, "create_harness", lambda *args, **kwargs: object())
    monkeypatch.setattr(_case_test.RuntimeSecretStore, "from_settings", lambda *args: object())
    monkeypatch.setattr(_case_test, "run_strict_lifecycle_case", lambda **kwargs: ReportArtifact(run_id="run", path=run_dir / "execution-result.json"))
    monkeypatch.setattr(_case_test, "load_run_metadata", lambda path: SimpleNamespace(execution_result="execution-result.json", status="success", result=RunResultSummary(summary="Completed.")))
    monkeypatch.setattr(_case_test, "_atomic_write", lambda *args: (_ for _ in ()).throw(OSError("disk full")))
    result = _case_test.execute_case_test(CaseTestRequest(current_directory=tmp_path, platform="web", case_path=source, suggest=True), suggestion_analyzer_factory=None)
    assert result.status == "success"
    assert result.processing["suggestion"]["persistence"] == "unavailable"
    assert result.run_id == "run"
