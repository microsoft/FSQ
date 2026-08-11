# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from pathlib import Path

from fsq_agent.config import Settings
from fsq_agent.doctor import DoctorService
from fsq_agent.models import (
	DiagnosticProbeResult,
	DoctorCheckResult,
	DoctorFix,
	DoctorProgressEvent,
	DoctorRequest,
	HarnessSettings,
)


class _Prompter:
	def __init__(self, *, accept: bool) -> None:
		self.accept = accept
		self.confirm_messages: list[str] = []

	def choose(self, message, options, default):
		return default

	def confirm(self, message, default=True):
		self.confirm_messages.append(message)
		return self.accept

	def text(self, message):
		raise AssertionError("no environment input expected")


class _PlatformProbe:
	def probe(self, timeout_seconds=5.0):
		return [
			DiagnosticProbeResult(
				id="web.ready",
				category="Web",
				status="pass",
				summary="Web is ready.",
			)
		]


class _PlatformFactory:
	def create(self, platform, harness_settings, progress_sink=None):
		return _PlatformProbe()


class _Provider:
	def probe(self, settings, timeout_seconds=5.0, progress_sink=None):
		return [
			DiagnosticProbeResult(
				id="provider.ready",
				category="Provider",
				status="pass",
				summary="Provider is ready.",
			)
		]

	def refresh_cached_copilot_provider_token(self, settings, progress_sink=None):
		raise AssertionError("provider refresh was not requested")


def _inspector(platform, workspace, environ, progress_sink=None):
	settings = Settings(harness=HarnessSettings(platform="web"))
	settings.workspace.root_dir = workspace
	return settings, [
		DiagnosticProbeResult(
			id="config.valid",
			category="Configuration",
			status="pass",
			summary="Configuration is valid.",
		)
	]


def _service(
	events: list[DoctorProgressEvent],
	*,
	accept: bool,
) -> DoctorService:
	return DoctorService(
		provider_diagnostics=_Provider(),
		platform_probe_factory=_PlatformFactory(),
		prompter=_Prompter(accept=accept),
		config_inspector=_inspector,
		progress_sink=events.append,
		environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
	)


def _workspace_events(events: list[DoctorProgressEvent]) -> list[DoctorProgressEvent]:
	return [
		event
		for event in events
		if event.check_id == "workspace.initialized"
		or event.repair is not None and event.repair.target == "workspace.initialized"
		or event.event_type == "repair_started"
		and event.summary.endswith("workspace.initialized...")
	]


def test_interactive_workspace_repair_is_verified_before_configuration(
	tmp_path: Path,
) -> None:
	events: list[DoctorProgressEvent] = []

	report = _service(events, accept=True).run(
		DoctorRequest(platform="web", interactive=True, working_directory=tmp_path)
	)

	workspace_events = _workspace_events(events)
	assert [event.event_type for event in workspace_events] == [
		"check_started",
		"action_required",
		"repair_started",
		"repair_completed",
		"check_started",
		"check_completed",
	]
	assert all(
		event.status != "fail"
		for event in workspace_events
		if event.event_type != "action_required"
	)
	assert workspace_events[-1].check is not None
	assert workspace_events[-1].check.status == "pass"
	configuration_index = next(
		index
		for index, event in enumerate(events)
		if event.event_type == "phase_started" and event.phase == "Configuration"
	)
	assert events.index(workspace_events[-1]) < configuration_index
	assert report.status == "ready"
	assert report.checks[0].id != "workspace.initialized" or report.checks[0].status == "pass"
	assert next(check for check in report.checks if check.id == "workspace.initialized").status == "pass"


def test_declined_workspace_repair_publishes_original_failure(tmp_path: Path) -> None:
	events: list[DoctorProgressEvent] = []
	prompter = _Prompter(accept=False)

	service = _service(events, accept=False)
	service.prompter = prompter
	report = service.run(
		DoctorRequest(platform="web", interactive=True, working_directory=tmp_path)
	)

	workspace_events = _workspace_events(events)
	assert [event.event_type for event in workspace_events] == [
		"check_started",
		"action_required",
		"repair_started",
		"repair_completed",
		"check_completed",
	]
	assert workspace_events[-1].check is not None
	assert workspace_events[-1].check.status == "fail"
	assert report.status == "blocked"
	assert report.repairs[0].status == "declined"
	assert prompter.confirm_messages == [
		"Apply repair: Initialize the current-directory fsq-agent workspace."
	]


def test_noninteractive_diagnosis_has_no_action_required(tmp_path: Path) -> None:
	events: list[DoctorProgressEvent] = []

	report = _service(events, accept=True).run(
		DoctorRequest(platform="web", working_directory=tmp_path)
	)

	assert all(event.event_type != "action_required" for event in events)
	assert next(check for check in report.checks if check.id == "workspace.initialized").status == "fail"
	assert report.repairs == []


def test_noninteractive_repair_is_immediate_without_action_required(tmp_path: Path) -> None:
	events: list[DoctorProgressEvent] = []

	report = _service(events, accept=False).run(
		DoctorRequest(platform="web", repair=True, working_directory=tmp_path)
	)

	assert all(event.event_type != "action_required" for event in events)
	workspace_events = _workspace_events(events)
	assert workspace_events[-1].check is not None
	assert workspace_events[-1].check.status == "pass"
	assert report.repairs[0].status == "applied"
	assert report.repairs[0].rerun_check_ids == ["workspace.initialized"]


def _run_workspace_verifier_failure(tmp_path: Path, *, interrupt: bool):
	service = _service([], accept=True)
	original_workspace_check = service._workspace_check
	calls = 0

	def workspace_check(workspace):
		nonlocal calls
		calls += 1
		if calls > 1:
			if interrupt:
				raise KeyboardInterrupt()
			raise RuntimeError("workspace verification failed")
		return original_workspace_check(workspace)

	service._workspace_check = workspace_check
	return service.run(
		DoctorRequest(platform="web", interactive=True, working_directory=tmp_path)
	)


def test_workspace_verification_exception_preserves_original_failure(tmp_path: Path) -> None:
	report = _run_workspace_verifier_failure(tmp_path, interrupt=False)

	assert report.status == "blocked"
	assert report.exit_code == 1
	assert next(check for check in report.checks if check.id == "workspace.initialized").status == "fail"
	assert report.repairs[0].status == "applied"
	assert report.repairs[0].rerun_check_ids == []


def test_workspace_verification_interruption_returns_cancelled_report(tmp_path: Path) -> None:
	report = _run_workspace_verifier_failure(tmp_path, interrupt=True)

	assert report.status == "cancelled"
	assert report.exit_code == 130
	assert next(check for check in report.checks if check.id == "workspace.initialized").status == "fail"
	assert report.repairs[0].status == "applied"
	assert report.repairs[0].rerun_check_ids == []


class _UnverifiedProvider:
	def __init__(self, *, verification_failure: str | None) -> None:
		self.probes = 0
		self.verification_failure = verification_failure

	def probe(self, settings, timeout_seconds=5.0, progress_sink=None):
		self.probes += 1
		if self.probes > 1:
			if self.verification_failure == "error":
				raise RuntimeError("verification failed")
			if self.verification_failure == "interrupt":
				raise KeyboardInterrupt()
			return []
		return [
			DiagnosticProbeResult(
				id="provider.credentials",
				category="Provider",
				status="fail",
				summary="Provider credentials are not ready.",
				fixes=[
					DoctorFix(
						description="Refresh provider credentials.",
						repair_action="provider.refresh_copilot_token",
					)
				],
			)
		]

	def refresh_cached_copilot_provider_token(self, settings, progress_sink=None):
		return None


def _run_unverified_provider(tmp_path: Path, *, verification_failure: str | None):
	workspace = tmp_path / ".fsq-agent-workspace"
	workspace.mkdir()
	(workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")
	return DoctorService(
		provider_diagnostics=_UnverifiedProvider(verification_failure=verification_failure),
		platform_probe_factory=_PlatformFactory(),
		prompter=_Prompter(accept=True),
		config_inspector=_inspector,
		environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
	).run(DoctorRequest(platform="web", interactive=True, working_directory=tmp_path))


def test_omitted_verification_result_preserves_original_failure(tmp_path: Path) -> None:
	report = _run_unverified_provider(tmp_path, verification_failure=None)

	assert report.status == "blocked"
	assert next(check for check in report.checks if check.id == "provider.credentials").status == "fail"
	assert report.repairs[0].rerun_check_ids == []


def test_verification_exception_preserves_original_failure(tmp_path: Path) -> None:
	report = _run_unverified_provider(tmp_path, verification_failure="error")

	assert report.status == "blocked"
	assert next(check for check in report.checks if check.id == "provider.credentials").status == "fail"
	assert report.repairs[0].rerun_check_ids == []


def test_verification_interruption_returns_cancelled_report_with_original_failure(
	tmp_path: Path,
) -> None:
	report = _run_unverified_provider(tmp_path, verification_failure="interrupt")

	assert report.status == "cancelled"
	assert report.exit_code == 130
	assert next(check for check in report.checks if check.id == "provider.credentials").status == "fail"
	assert report.repairs[0].status == "applied"
	assert report.repairs[0].rerun_check_ids == []


class _EmittingProvider(_UnverifiedProvider):
	def probe(self, settings, timeout_seconds=5.0, progress_sink=None):
		self.probes += 1
		status = "fail" if self.probes == 1 else "pass"
		check = DiagnosticProbeResult(
			id="provider.credentials",
			category="Provider",
			status=status,
			summary="Provider credentials are ready." if status == "pass" else "Provider credentials are not ready.",
			fixes=[]
			if status == "pass"
			else [
				DoctorFix(
					description="Refresh provider credentials.",
					repair_action="provider.refresh_copilot_token",
				)
			],
		)
		if progress_sink is not None:
			normalized = DoctorCheckResult.model_validate(check.model_dump())
			progress_sink(
				DoctorProgressEvent(
					event_type="check_completed",
					phase=normalized.category,
					check_id=normalized.id,
					status=normalized.status,
					summary=normalized.summary,
					check=normalized,
				)
			)
		if self.probes == 1:
			return [check]
		if self.verification_failure == "error":
			raise RuntimeError("after completion")
		if self.verification_failure == "interrupt":
			raise KeyboardInterrupt()
		return []


def _run_emitting_provider(tmp_path: Path, *, verification_failure: str | None):
	workspace = tmp_path / ".fsq-agent-workspace"
	workspace.mkdir()
	(workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")
	return DoctorService(
		provider_diagnostics=_EmittingProvider(verification_failure=verification_failure),
		platform_probe_factory=_PlatformFactory(),
		prompter=_Prompter(accept=True),
		config_inspector=_inspector,
		environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
	).run(DoctorRequest(platform="web", interactive=True, working_directory=tmp_path))


def test_emitted_verification_survives_omitted_return(tmp_path: Path) -> None:
	report = _run_emitting_provider(tmp_path, verification_failure=None)

	assert next(check for check in report.checks if check.id == "provider.credentials").status == "pass"
	assert report.repairs[0].rerun_check_ids == ["provider.credentials"]


def test_emitted_verification_survives_later_exception(tmp_path: Path) -> None:
	report = _run_emitting_provider(tmp_path, verification_failure="error")

	assert next(check for check in report.checks if check.id == "provider.credentials").status == "pass"
	assert report.repairs[0].rerun_check_ids == ["provider.credentials"]


def test_emitted_verification_survives_later_interruption(tmp_path: Path) -> None:
	report = _run_emitting_provider(tmp_path, verification_failure="interrupt")

	assert report.status == "cancelled"
	assert next(check for check in report.checks if check.id == "provider.credentials").status == "pass"
	assert report.repairs[0].rerun_check_ids == ["provider.credentials"]


def test_provider_retry_does_not_reemit_settled_checks(tmp_path: Path) -> None:
	class Provider(_EmittingProvider):
		def probe(self, settings, timeout_seconds=5.0, progress_sink=None):
			settled = DiagnosticProbeResult(
				id="provider.configuration",
				category="Provider",
				status="pass",
				summary="Provider configuration is valid.",
			)
			if progress_sink is not None:
				normalized = DoctorCheckResult.model_validate(settled.model_dump())
				progress_sink(
					DoctorProgressEvent(
						event_type="check_completed",
						check_id=normalized.id,
						check=normalized,
					)
				)
			return [settled, *super().probe(settings, timeout_seconds, progress_sink)]

	workspace = tmp_path / ".fsq-agent-workspace"
	workspace.mkdir()
	(workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")
	events: list[DoctorProgressEvent] = []
	report = DoctorService(
		provider_diagnostics=Provider(verification_failure=None),
		platform_probe_factory=_PlatformFactory(),
		prompter=_Prompter(accept=True),
		config_inspector=_inspector,
		progress_sink=events.append,
		environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
	).run(DoctorRequest(platform="web", interactive=True, working_directory=tmp_path))

	settled_completions = [
		event
		for event in events
		if event.event_type == "check_completed"
		and event.check_id == "provider.configuration"
	]
	assert len(settled_completions) == 1
	assert next(check for check in report.checks if check.id == "provider.configuration").status == "pass"


class _InitiallyFailingProvider:
	def probe(self, settings, timeout_seconds=5.0, progress_sink=None):
		raise RuntimeError("initial provider failure")


def test_initial_provider_exception_remains_blocking_check(tmp_path: Path) -> None:
	workspace = tmp_path / ".fsq-agent-workspace"
	workspace.mkdir()
	(workspace / ".fsq-agent-workspace").write_text("ok", encoding="utf-8")

	report = DoctorService(
		provider_diagnostics=_InitiallyFailingProvider(),
		platform_probe_factory=_PlatformFactory(),
		config_inspector=_inspector,
		environ={"FSQ_WEB_BROWSER_EXECUTABLE_PATH": "chrome.exe"},
	).run(DoctorRequest(platform="web", interactive=True, working_directory=tmp_path))

	assert report.status == "blocked"
	assert next(check for check in report.checks if check.id == "provider.probe").status == "fail"
