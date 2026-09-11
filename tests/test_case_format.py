# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from fsq_agent._capability_bootstrap import build_capability_registry
from fsq_agent.adapters.cli import main
from fsq_agent.case_dsl import FsqCaseLoader, FsqCaseSerializer
from fsq_agent.models import ConfigurationError

HEADER = "schemaVersion: fsq.ai-test/v1\nname: example\nplatform: web\n"


def serialize(text):
    case = FsqCaseLoader().load_text(text, Path("example.fsq.yaml"))
    return FsqCaseSerializer(build_capability_registry(platform="web").snapshot()).serialize(case)


def test_equivalent_params_and_idempotence():
    first = serialize(HEADER + "---\n- typeText: {text: hello, target: Search, locator: null}\n")
    second = serialize(HEADER + "---\n- typeText: {target: Search, textType: literal, text: hello}\n")
    assert first == second
    assert serialize(first.decode()) == first
    assert b"null" not in first


def test_free_null_and_hook_order_and_timeout():
    source = HEADER + "properties: {z: null, a: false}\nonCaseStart:\n  runShell: prepare\n  runCase: login.fsq.yaml\n---\n- startBrowser: {timeout: 123}\n"
    output = serialize(source)
    assert b"z: null" in output
    assert output.index(b"runShell") < output.index(b"runCase")
    assert b"timeout: 123" in output
    assert serialize(output.decode()) == output


@pytest.mark.parametrize("tail", ["---\n- nonexistent: {}\n", "---\n- startBrowser: {timeout: false}\n", "name: duplicate\n", "properties: &p {self: *p}\n"])
def test_invalid_input(tail):
    with pytest.raises(ConfigurationError):
        serialize(HEADER + tail)


def test_cli_check_diff_write_without_workspace(tmp_path):
    path = tmp_path / "case.fsq.yaml"
    path.write_text(HEADER + "---\n- startBrowser\n")
    runner = CliRunner()
    checked = runner.invoke(main, ["case", "format", str(path), "--json"])
    assert checked.exit_code == 1, checked.output
    assert json.loads(checked.output)["result"]["valid"] is True
    before = path.read_bytes()
    diff = runner.invoke(main, ["case", "format", str(path), "--diff", "--json"])
    assert diff.exit_code == 1
    assert json.loads(diff.output)["result"]["diff"]
    assert path.read_bytes() == before
    written = runner.invoke(main, ["case", "format", str(path), "--write", "--json"])
    assert written.exit_code == 0, written.output
    assert json.loads(written.output)["result"]["changed"] is True
    modified = path.stat().st_mtime_ns
    assert runner.invoke(main, ["case", "format", str(path), "--write"]).exit_code == 0
    assert path.stat().st_mtime_ns == modified


def test_cli_invalid_and_usage_preserve_source(tmp_path):
    path = tmp_path / "case.fsq.yaml"
    path.write_text(HEADER + "---\n- navigateTo: {}\n")
    before = path.read_bytes()
    result = CliRunner().invoke(main, ["case", "format", str(path), "--write", "--json"])
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["result"]["valid"] is False
    assert path.read_bytes() == before
    result = CliRunner().invoke(main, ["case", "format", str(path), "--check", "--write", "--json"])
    assert result.exit_code == 2
    assert json.loads(result.output)["type"] == "error"


def test_publication_never_replaces_different_case(tmp_path):
    from fsq_agent.execution import publish_recorded_case

    source = tmp_path / "recorded.fsq.yaml"
    source.write_text(HEADER + "---\n- startBrowser: {}\n")
    args = {"candidate_path": source, "destination_directory": tmp_path / "cases", "platform": "web", "case_name": "stable"}
    destination, outcome = publish_recorded_case(**args)
    assert outcome == "created"
    assert b"name: stable" in destination.read_bytes()
    timestamp = destination.stat().st_mtime_ns
    assert publish_recorded_case(**args)[1] == "unchanged"
    assert destination.stat().st_mtime_ns == timestamp
    destination.write_text("existing user case")
    assert publish_recorded_case(**args)[1] == "conflict"
    assert destination.read_text() == "existing user case"


def test_ordered_steps_and_actual_values_produce_differences():
    first = serialize(HEADER + "---\n- startBrowser: {}\n- waitMs: {duration_ms: 1}\n")
    assert first != serialize(HEADER + "---\n- waitMs: {duration_ms: 1}\n- startBrowser: {}\n")
    assert first != serialize(HEADER + "---\n- startBrowser: {}\n- waitMs: {duration_ms: 2}\n")


def test_static_format_does_not_use_workspace_or_provider(tmp_path, monkeypatch):
    from fsq_agent.application import CaseFormatRequest, format_case

    def forbidden(*args, **kwargs):
        raise AssertionError("Runtime operation during static formatting")

    monkeypatch.setattr("fsq_agent.application.workspace.require_initialized_workspace", forbidden)
    monkeypatch.setattr("fsq_agent.ai_services.build_ai_assertion_evaluator", forbidden)
    path = tmp_path / "case.fsq.yaml"
    path.write_text(HEADER + "onCaseStart: {runCase: missing.fsq.yaml}\n---\n- assertWithAI: {prompt: verify}\n")
    result = format_case(CaseFormatRequest(current_directory=tmp_path, case_path=path))
    assert result.valid


def test_canonical_preserves_required_null_and_falsy_data():
    from pydantic import BaseModel

    class Params(BaseModel):
        required_null: str | None
        optional_null: str | None = None
        enabled: bool = False
        amount: int = 0
        text: str = ""

    # Exercise the public serializer with a declarative parameter model.
    registry = build_capability_registry(platform="web").snapshot()
    capability = registry.resolve("startBrowser").model_copy(update={"params_model": Params})
    snapshot = registry.model_copy(update={"capabilities": (capability,)})
    case = FsqCaseLoader().load_text(HEADER + "---\n- startBrowser: {required_null: null}\n", Path("case.fsq.yaml"))
    output = FsqCaseSerializer(snapshot).serialize(case)
    assert b"required_null: null" in output
    assert b"optional_null" not in output
    assert b"enabled: false" in output
    assert b"amount: 0" in output


def test_format_interruption_exits_130(monkeypatch):
    def interrupt(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr("fsq_agent.adapters.cli._main.format_case", interrupt)
    result = CliRunner().invoke(main, ["case", "format", "case.fsq.yaml", "--json"])
    assert result.exit_code == 130


def test_legacy_format_warns_without_renaming(tmp_path):
    path = tmp_path / "case.codex.yaml"
    path.write_text(HEADER)
    result = CliRunner().invoke(main, ["case", "format", str(path), "--json"])
    assert json.loads(result.output)["warnings"][0].startswith("case.suffix_deprecated")
    assert path.exists()


@pytest.mark.parametrize(
    ("extra", "field", "reason"),
    [
        ("properties: {when: 2026-01-01}\n", ["properties", "when"], "Unsupported Case value"),
        ("properties: {a: 1, a: 2}\n", ["properties", "a"], "Duplicate YAML key"),
        ("properties: &x {self: *x}\n", ["properties", "self"], "Recursive YAML"),
    ],
)
def test_diagnostics_are_addressable(tmp_path, extra, field, reason):
    path = tmp_path / "case.fsq.yaml"
    path.write_text(HEADER + extra)
    result = CliRunner().invoke(main, ["case", "format", str(path), "--json"])
    diagnostic = json.loads(result.output)["result"]["diagnostics"][0]
    assert diagnostic["field_path"] == field
    assert reason in diagnostic["message"]


def test_schema_error_does_not_expose_rejected_value():
    with pytest.raises(ConfigurationError) as caught:
        FsqCaseLoader().load_text(HEADER.replace("fsq.ai-test/v1", "REJECTED_SECRET"), Path("case.fsq.yaml"))
    assert caught.value.context["field_path"] == ["schemaVersion"]
    assert "REJECTED_SECRET" not in str(caught.value.context)


def test_write_uses_portable_permissions(tmp_path, monkeypatch):
    import os

    from fsq_agent.application import CaseFormatRequest, format_case

    monkeypatch.delattr(os, "fchmod", raising=False)
    path = tmp_path / "case.fsq.yaml"
    path.write_text(HEADER)
    mode = path.stat().st_mode
    result = format_case(CaseFormatRequest(current_directory=tmp_path, case_path=path, mode="write"))
    assert result.changed
    assert path.stat().st_mode == mode


def test_public_validation_checks_schema_and_registry():
    from fsq_agent.case_dsl import FsqCaseValidator

    case = FsqCaseLoader().load_text(HEADER, Path("case.fsq.yaml"))
    registry = build_capability_registry(platform="web").snapshot()
    invalid = case.model_copy(update={"config": case.config.model_copy(update={"schema_version": "unsupported"})})
    with pytest.raises(ConfigurationError):
        FsqCaseValidator(registry).validate(invalid)
    with pytest.raises(ConfigurationError):
        FsqCaseSerializer(registry).serialize(invalid)
    ambiguous = registry.model_copy(update={"capabilities": (*registry.capabilities, registry.capabilities[0])})
    with pytest.raises(ConfigurationError):
        FsqCaseValidator(ambiguous).validate(case)


@pytest.mark.parametrize("metadata", ["[]", "null", '{"draft": "false"}'])
def test_invalid_recording_metadata_fails_before_publication(tmp_path, metadata):
    from fsq_agent.application import CaseSaveRequest, save_recorded_case

    source = tmp_path / "recorded.fsq.yaml"
    source.write_text(HEADER)
    source.with_name("recording.json").write_text(metadata)
    with pytest.raises(ConfigurationError):
        save_recorded_case(CaseSaveRequest(candidate_path=source, destination_directory=tmp_path / "cases", platform="web", case_name="stable"))
    assert not (tmp_path / "cases" / "stable.fsq.yaml").exists()


@pytest.mark.parametrize("hook_field", ["on_case_start", "on_case_complete"])
def test_public_hook_duplicate_actions_survive_roundtrip(hook_field):
    from fsq_agent.models import FsqCaseHook, FsqCaseHookAction

    case = FsqCaseLoader().load_text(HEADER, Path("repeated.fsq.yaml"))
    actions = [("runShell", "one"), ("runCase", "login.fsq.yaml"), ("runShell", "two"), ("runShell", "three")]
    setattr(case.config, hook_field, [FsqCaseHook(actions=[FsqCaseHookAction(action_name=name, value=value) for name, value in actions])])
    serializer = FsqCaseSerializer(build_capability_registry(platform="web").snapshot())
    output = serializer.serialize(case)
    reloaded = FsqCaseLoader().load_text(output.decode(), case.path)
    assert [(a.action_name, a.value) for hook in getattr(reloaded.config, hook_field) for a in hook.actions] == actions
    assert serializer.serialize(reloaded) == output
    assert len(getattr(case.config, hook_field)) == 1


@pytest.mark.parametrize("value", ["2026-01-01", ".nan"])
@pytest.mark.parametrize("operation", ["validate", "serialize"])
def test_public_metadata_diagnostics_include_source_path(value, operation):
    from fsq_agent.case_dsl import FsqCaseValidator

    path = Path("specific.fsq.yaml")
    case = FsqCaseLoader().load_text(HEADER + f"properties: {{value: {value}}}\n", path)
    snapshot = build_capability_registry(platform="web").snapshot()
    service = FsqCaseValidator(snapshot) if operation == "validate" else FsqCaseSerializer(snapshot)
    with pytest.raises(ConfigurationError) as caught:
        getattr(service, operation)(case)
    assert caught.value.context == {"path": str(path), "code": "case.value", "field_path": ["properties", "value"]}
