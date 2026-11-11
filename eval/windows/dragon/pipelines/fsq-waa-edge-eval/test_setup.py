# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Protects readiness/auth decisions, default non-scoring behavior, and Setup reporting without local native UI execution.
import base64
import copy
import io
import json
import tempfile
import unittest
import zipfile
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from setup_acceptance import accept, needs_authorization, provider_ready, window_acceptance
from setup_report import new_checks, render_setup_report, setup_ready


def report():
    checks = new_checks()
    for row in checks[:4]:
        row.update(status="passed", detail="Prerequisite verified")
    return {"suite": "fsq-devbox-setup/v1", "status": "preparing", "setup_checks": checks, "tasks": [], "errors": [], "execution_machine": "REMOTE-HOST", "smoke": {"status": "not_requested"}}


class SetupTests(unittest.TestCase):
    def test_existing_ready_provider_is_preserved_and_real_connection_is_required(self):
        saved = {"configured": True, "provider": "azure_openai", "model": "existing"}
        with (
            patch("setup_acceptance.read_provider", return_value=saved),
            patch("setup_acceptance.test_connection", return_value={"status": "ready"}) as check,
            patch("setup_acceptance.run_live") as auth,
        ):
            result = provider_ready(Path(), True, "different")
        self.assertEqual(result["provider"], "azure_openai")
        self.assertEqual(result["model"], "existing")
        self.assertFalse(result["authorization_performed"])
        check.assert_called_once()
        auth.assert_not_called()

    def test_only_missing_or_github_auth_failure_allows_device_authorization(self):
        self.assertTrue(needs_authorization({"configured": False}, {}))
        for code in (401, 403):
            self.assertTrue(needs_authorization({"configured": True, "provider": "github_copilot"}, {"status_code": code}))
        for provider, code in (("azure_openai", 401), ("google_gemini", 403), ("github_copilot", 429), ("github_copilot", 500)):
            self.assertFalse(needs_authorization({"configured": True, "provider": provider}, {"status_code": code}))

    def test_missing_provider_can_authorize_then_must_recheck_actual_connection(self):
        with (
            patch("setup_acceptance.read_provider", return_value={"configured": False}),
            patch("setup_acceptance.run_live", return_value=({"exit_code": 0, "timed_out": False}, Path("auth.log"))) as auth,
            patch("setup_acceptance.read_provider_after_auth", return_value={"provider": "github_copilot", "model": "selected"}),
            patch("setup_acceptance.test_connection", return_value={"status": "ready"}) as check,
        ):
            result = provider_ready(Path(), True, "selected")
        self.assertTrue(result["authorization_performed"])
        self.assertEqual(auth.call_args.args[0][-2:], ["authorize", "selected"])
        check.assert_called_once_with(Path(), "provider-connection-after-auth")

    def test_disabled_auth_or_different_provider_failure_does_not_change_credentials(self):
        for saved, allow in (({"configured": False}, False), ({"configured": True, "provider": "azure_openai"}, True)):
            with (
                self.subTest(saved=saved),
                patch("setup_acceptance.read_provider", return_value=saved),
                patch("setup_acceptance.test_connection", return_value={"status": "error", "status_code": 401, "message": "auth failed"}),
                patch("setup_acceptance.run_live") as auth,
            ):
                with pytest.raises(RuntimeError):
                    provider_ready(Path(), allow, "model")
                auth.assert_not_called()

    def test_expired_github_credentials_reauthorize_without_switching_saved_model(self):
        saved = {"configured": True, "provider": "github_copilot", "model": "saved-model"}
        with (
            patch("setup_acceptance.read_provider", return_value=saved),
            patch("setup_acceptance.test_connection", side_effect=[{"status": "error", "status_code": 401, "message": "expired"}, {"status": "ready"}]),
            patch("setup_acceptance.run_live", return_value=({"exit_code": 0, "timed_out": False}, Path("auth.log"))) as auth,
            patch("setup_acceptance.read_provider_after_auth", return_value=saved),
        ):
            provider_ready(Path(), True, "default-for-new-accounts")
        self.assertEqual(auth.call_args.args[0][-1], "saved-model")

    def test_authorization_timeout_does_not_mark_remote_provider_ready(self):
        with (
            patch("setup_acceptance.read_provider", return_value={"configured": False}),
            patch("setup_acceptance.run_live", return_value=({"exit_code": 1, "timed_out": True}, Path("auth.log"))),
            patch("setup_acceptance.read_provider_after_auth") as after,
        ):
            with pytest.raises(RuntimeError, match="did not complete"):
                provider_ready(Path(), True, "model")
            after.assert_not_called()

    def test_setup_default_success_has_no_case_score_and_persists_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".fsq-waa").mkdir()
            value = report()
            with (
                patch("setup_acceptance.provider_ready", return_value={"provider": "github_copilot", "model": "selected"}),
                patch("setup_acceptance.require_desktop"),
                patch("setup_acceptance.require_screen_capture"),
                patch("setup_acceptance.find_edge", return_value=Path("X:\\Edge\\msedge.exe")),
                patch("setup_acceptance.window_acceptance", return_value={"cleanup": "settled"}),
                patch("setup_acceptance.Path.home", return_value=root),
            ):
                result = accept(root, value)
            self.assertEqual(result, 0)
            self.assertTrue(setup_ready(value))
            self.assertEqual(value["status"], "setup_ready")
            self.assertEqual(value["tasks"], [])
            self.assertEqual(value["smoke"]["status"], "not_requested")
            marker = json.loads((root / ".fsq-waa" / "setup-ready.json").read_text())
            self.assertEqual(marker["status"], "setup_ready")
            self.assertEqual(marker["tasks"], [])

    def test_unavailable_screen_blocks_readiness_and_does_not_start_test_window(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = report()
            with (
                patch("setup_acceptance.provider_ready", return_value={"provider": "github_copilot", "model": "model"}),
                patch("setup_acceptance.require_desktop"),
                patch("setup_acceptance.require_screen_capture", side_effect=RuntimeError("screen unavailable")),
                patch("setup_acceptance.window_acceptance") as window,
            ):
                result = accept(root, value)
            self.assertEqual(result, 1)
            self.assertEqual(value["status"], "blocked")
            self.assertEqual(next(row["status"] for row in value["setup_checks"] if row["id"] == "desktop"), "blocked")
            self.assertFalse(setup_ready(value))
            window.assert_not_called()
            self.assertTrue((root / "evidence.zip").exists())

    def test_provider_error_blocks_before_native_desktop_and_keeps_cause(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = report()
            with patch("setup_acceptance.provider_ready", side_effect=RuntimeError("actual provider rejected request")), patch("setup_acceptance.require_desktop") as desktop:
                self.assertEqual(accept(root, value), 1)
            desktop.assert_not_called()
            self.assertEqual(value["errors"][0]["message"], "actual provider rejected request")
            self.assertEqual(value["tasks"], [])

    def test_native_window_probe_owns_cleanup_even_when_screenshot_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".fsq-waa").mkdir()
            session = Mock()
            session.pid = 42
            session.profile = root / "profile"
            session.executable = root / "msedge.exe"
            driver = Mock()
            driver.context.return_value = {"session_id": "test"}
            driver.press_key.return_value = {"status": "passed"}
            driver.ui_snapshot.return_value = {"nodes": []}
            driver.screenshot.side_effect = OSError("capture failed")
            with (
                patch("setup_acceptance.Path.home", return_value=root),
                patch("setup_acceptance.desktop_lock", return_value=nullcontext()),
                patch("setup_acceptance.EdgeSession", return_value=session),
                patch("setup_acceptance.install_process_scope"),
                patch("fsq_agent.drivers.windows._pywinauto.PywinautoWindowsDriver", return_value=driver),
                pytest.raises(OSError, match="capture failed"),
            ):
                window_acceptance(root, session.executable)
            driver.close.assert_called_once()
            session.stop.assert_called_once()
            self.assertEqual(driver.press_key.call_count, 2)

    def test_setup_report_is_not_a_case_success_rate_and_escapes_diagnostics(self):
        value = report()
        value["setup_checks"][4].update(status="blocked", detail="<script>unsafe</script>")
        value["status"] = "blocked"
        with tempfile.TemporaryDirectory() as directory:
            page = render_setup_report(Path(directory), value)
        self.assertIn("FSQ Dev Box 接入报告", page)
        self.assertIn("不是测试集成功率", page)
        self.assertIn("未运行\uff08默认只验收环境\uff09", page)
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("4 / 8", page)

    def test_setup_ready_requires_every_prerequisite_not_only_exit_zero(self):
        value = report()
        self.assertFalse(setup_ready(value))
        for row in value["setup_checks"]:
            row["status"] = "passed"
        self.assertTrue(setup_ready(value))
        self.assertFalse(setup_ready({"setup_checks": []}))
        self.assertFalse(setup_ready({"setup_checks": value["setup_checks"][:-1]}))
        for failed in value["setup_checks"]:
            candidate = copy.deepcopy(value)
            next(row for row in candidate["setup_checks"] if row["id"] == failed["id"])["status"] = "blocked"
            self.assertFalse(setup_ready(candidate))

    def test_setup_manifest_is_remote_setup_not_service_registration_or_default_case_execution(self):
        path = Path(__file__).parent.parent / "fsq-dev-box-setup" / "pipeline.json"
        manifest = json.loads(path.read_text())
        values = {row["name"]: row for row in manifest["variables"]}
        self.assertEqual(values["setup-smoke"]["default"], "false")
        self.assertEqual(values["git-branch"]["default"], "user/ty/win-eval-dragon")
        self.assertNotIn("fsq-python", values)
        self.assertNotIn("dataset-dir", values)
        self.assertEqual(len(manifest["stages"]), 1)

    def test_setup_shipped_payload_is_current_and_pinned_to_non_scoring_setup_mode(self):
        from build_remote import FILES

        package = Path(__file__).parent
        # Generated deploy scripts are output only, not maintained source.
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from build import build

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            build(output)
            script = (output / "pipelines/fsq-dev-box-setup/deploy.ps1").read_text(encoding="utf-8")
        self.assertIn("$env:DRAGON_VAR_OPERATION_MODE = 'setup'", script)
        payload = base64.b64decode(script.split("[Convert]::FromBase64String('", 1)[1].split("')", 1)[0])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in FILES:
                self.assertEqual(archive.read(name), (package / name).read_bytes(), f"Rebuild Setup payload: {name}")

    def test_optional_smoke_preserves_environment_result_and_bundles_actual_case_evidence(self):
        from portable_bootstrap import run_setup_smoke
        from reporting import save_json

        for outcome, expected in (("passed", 0), ("failed", 1), ("blocked", 1)):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                root = work / "waa-eval"
                root.mkdir()
                value = report()
                value["status"] = "setup_ready"
                for check in value["setup_checks"]:
                    check["status"] = "passed"
                source = {"url": "https://github.com/microsoft/FSQ.git", "branch": "user/ty/win-eval-dragon", "commit": "a" * 40}

                def stage(python, operation, evidence, environment, outcome=outcome, expected=expected):
                    save_json(evidence / "results.json", {"status": outcome, "summary": {"benchmark_passed": int(outcome == "passed")}})
                    (evidence / "create.command.json").write_text('{"operation":"case.create"}')
                    self.assertEqual(environment["DRAGON_VAR_CASE_IDS"], "1a1ec621-b675-4099-96a9-f702dc27afb4-wos")
                    return expected if operation == "run" or outcome == "blocked" else 0

                with patch("portable_bootstrap.execute_stage", side_effect=stage):
                    status = run_setup_smoke(Path("python.exe"), work, root, {}, source, value)
                self.assertEqual(status, expected)
                self.assertEqual(value["status"], "setup_ready")
                self.assertEqual(value["smoke"]["status"], outcome)
                self.assertEqual(value["tasks"], [])
                self.assertTrue((root / "smoke-evidence" / "create.command.json").exists())
                with zipfile.ZipFile(root / "evidence.zip") as archive:
                    self.assertIn("smoke-evidence/create.command.json", archive.namelist())


if __name__ == "__main__":
    unittest.main()
