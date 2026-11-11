# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Protects Dataset admission, independent scoring, evidence rendering, and local Windows lifecycle guards.
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from dataset import DEFAULT_IDS, load_dataset, score_preferences, seed_profile, validate_selected
from edge_session import EdgeSession, bounded_root, desktop_lock, detach_owned, edge_arguments, install_process_scope, visible_owned_handle
from reporting import command_details, dashboard_charts, donut_chart, format_seconds, render_report, summarize, verdict, wall_duration, write_report
from runner import classify_evaluation, fsq_call, workspace_path


def task(getter="profile_name_from_edge"):
    return {
        "id": DEFAULT_IDS[0],
        "snapshot": "msedge",
        "instruction": "Change the profile name to Thomas.",
        "config": [{"type": "launch", "parameters": {"command": ["start", "msedge", "--remote-debugging-port=1337"]}}],
        "evaluator": {
            "func": "exact_match",
            "result": {"type": getter},
            "expected": {"type": "rule", "rules": {"expected": "Thomas"}},
        },
    }


class AdapterTests(unittest.TestCase):
    def test_report_first_screen_names_cases_statuses_and_success_denominator(self):
        getters = ("profile_name_from_edge", "edge_font_size", "edge_default_download_folder")
        observed = ("Thomas", 24, "C:\\")
        rows = [
            {
                "id": case_id,
                "instruction": "Synthetic task",
                "getter": getter,
                "fsq_status": "success",
                "evaluation_status": "valid",
                "benchmark_score": 1,
                "observed": value,
                "expected": {"expected": value},
                "cli": {"seconds": 85},
            }
            for case_id, getter, value in zip(DEFAULT_IDS, getters, observed, strict=True)
        ]
        inventory = [{"id": row["id"], "getter": row["getter"], "supported": True} for row in rows]
        inventory += [{"id": f"not-selected-{index}", "getter": "other", "supported": False} for index in range(10)]
        report = {"status": "passed", "tasks": rows, "inventory": inventory}
        with tempfile.TemporaryDirectory() as directory:
            page = render_report(Path(directory), report)
        first_screen = page.split('<section class="panel">', 1)[0]
        self.assertIn("本轮本地成功率", first_screen)
        self.assertIn("<strong>100%</strong>", first_screen)
        self.assertIn("3 个通过 / 3 个有效判分", first_screen)
        self.assertIn("测试集共 13 条\uff1b本轮选中 3 条\uff0c另外 10 条未执行", first_screen)
        table = page.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
        self.assertEqual(table.count("<tr>"), 3)
        for title in ("修改 Profile 名称", "调整默认字体大小", "修改下载目录"):
            self.assertIn(title, table)
        for case_id in DEFAULT_IDS:
            self.assertIn(case_id, table)
        self.assertEqual(table.count('class="badge pass">通过'), 3)
        self.assertIn("本轮未执行的 10 条 Case", page)

    def test_report_failure_unknown_and_blocked_do_not_disappear_or_inflate_rate(self):
        rows = [
            {"id": "pass", "instruction": "Pass", "evaluation_status": "valid", "benchmark_score": 1, "fsq_status": "success"},
            {"id": "fail", "instruction": "Fail", "evaluation_status": "valid", "benchmark_score": 0, "fsq_status": "success"},
            {"id": "blocked", "instruction": "Blocked", "evaluation_status": "blocked", "benchmark_score": None},
            {"id": "unknown", "instruction": "Unknown", "evaluation_status": "unknown", "benchmark_score": None},
            {"id": "pending", "instruction": "Pending", "evaluation_status": "not_run"},
        ]
        summary = summarize(rows)
        self.assertEqual(summary["benchmark_pass_rate"], 0.5)
        self.assertEqual((summary["benchmark_passed"], summary["benchmark_failed"], summary["unscored"]), (1, 1, 3))
        with tempfile.TemporaryDirectory() as directory:
            page = render_report(Path(directory), {"status": "incomplete", "tasks": rows})
        for text in ("50%", "1 个通过 / 2 个有效判分", "不通过", "环境阻塞", "结果未知", "未执行"):
            self.assertIn(text, page)
        self.assertNotIn("本轮选中的 Case 全部通过", page)

    def test_no_valid_evaluation_shows_unknown_rate_not_zero_percent(self):
        for status in ("preparing", "ready", "running", "blocked", "incomplete"):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                page = render_report(Path(directory), {"status": status, "tasks": []})
                self.assertIn("暂无法计算", page)
                self.assertIn("尚无选中的 Case", page)
                self.assertNotIn("<strong>0%</strong>", page)
        for score in (None, float("nan"), True, -1, 2):
            self.assertNotIn(verdict({"evaluation_status": "valid", "benchmark_score": score})[0], {"pass", "fail"})

    def test_report_escapes_all_user_controlled_visible_fields_and_preserves_full_values(self):
        unsafe = '<script>alert("x")</script>'
        row = {
            "id": "safe-id",
            "instruction": unsafe,
            "evaluation_status": "valid",
            "benchmark_score": 0,
            "observed": unsafe,
            "expected": {"expected": unsafe},
            "run_id": unsafe,
            "error": {"message": unsafe},
        }
        with tempfile.TemporaryDirectory() as directory:
            page = render_report(Path(directory), {"status": "failed", "tasks": [row], "errors": [{"message": unsafe}]})
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn('lang="zh-CN"', page)
        self.assertIn('name="viewport"', page)
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertNotIn("text-overflow:ellipsis", page)

    def test_report_narrow_browser_preserves_long_case_values_and_keyboard_details(self):
        from playwright.sync_api import sync_playwright

        row = {
            "id": "test-case",
            "instruction": "长任务名称" * 100 + "X" * 300,
            "fsq_status": "success",
            "evaluation_status": "valid",
            "benchmark_score": 1,
            "observed": "完整观测值" * 100 + "Y" * 300,
            "expected": {"expected": "Z" * 300},
        }
        with tempfile.TemporaryDirectory() as directory:
            page_html = render_report(Path(directory), {"status": "passed", "tasks": [row]})
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="chrome", headless=True)
            try:
                page = browser.new_page(viewport={"width": 390, "height": 900}, reduced_motion="reduce")
                page.set_content(page_html)
                self.assertEqual(page.locator("tbody tr").count(), 1)
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                summary = page.locator("#case-1 > summary")
                summary.focus()
                page.keyboard.press("Enter")
                self.assertIsNotNone(page.locator("#case-1").get_attribute("open"))
                self.assertTrue(page.evaluate("document.documentElement.scrollWidth <= innerWidth"))
                summary.focus()
                page.keyboard.press("Enter")
                self.assertIsNone(page.locator("#case-1").get_attribute("open"))
            finally:
                browser.close()

    def test_dashboard_donut_segment_sizes_and_legend_match_actual_counts(self):
        import xml.etree.ElementTree as ET

        element = ET.fromstring(  # noqa: S314 - Parse only this test's locally rendered constant SVG, never external XML.
            donut_chart(
                "结果",
                [("通过", 2, "pass"), ("不通过", 1, "fail"), ("未获结论", 1, "neutral")],
                4,
                "Case",
            )
        )
        segments = [node for node in element.iter("circle") if node.get("class") == "donut-segment"]
        self.assertEqual([node.get("data-count") for node in segments], ["2", "1", "1"])
        self.assertEqual([float(node.get("stroke-dasharray").split()[0]) for node in segments], [50, 25, 25])
        self.assertEqual([float(node.get("stroke-dashoffset")) for node in segments], [0, -50, -75])
        self.assertIn("通过 2", element.find(".//svg").get("aria-label"))
        empty = donut_chart("空结果", [("未获结论", 0, "neutral")], 0, "Case")
        self.assertNotIn("donut-segment", empty)
        self.assertNotIn("nan", empty)

    def test_dashboard_coverage_and_cli_timing_are_not_success_or_wall_duration(self):
        rows = [
            {"id": "a", "instruction": "A", "fsq_status": "success", "cli": {"seconds": 50}},
            {"id": "b", "instruction": "B", "fsq_status": "failed", "cli": {"seconds": 100}},
            {"id": "c", "instruction": "C", "fsq_status": "error", "cli": {"seconds": None}},
        ]
        inventory = [{"id": str(index)} for index in range(13)]
        charts = dashboard_charts(rows, inventory, {}, summarize(rows))
        self.assertIn("23.1%", charts)
        self.assertIn("不是通过率", charts)
        self.assertIn("已调用 FSQ 3 条 / 测试集 13 条", charts)
        self.assertIn('style="width:50.0000%" data-seconds="50"', charts)
        self.assertIn('style="width:100.0000%" data-seconds="100"', charts)
        self.assertIn("未记录", charts)
        self.assertNotIn("nan", charts)

    def test_dashboard_wall_duration_requires_recorded_timezone_aware_endpoints(self):
        report = {"started_at": "2026-11-10T05:54:40Z", "finished_at": "2026-11-10T06:03:11Z"}
        self.assertEqual(wall_duration(report), 511)
        self.assertEqual(format_seconds(511), "8 分 31 秒")
        self.assertEqual(format_seconds(119.9), "2 分 0 秒")
        for value in (
            {},
            {"started_at": "bad", "finished_at": "bad"},
            {"started_at": "2026-11-10T06:03:11Z", "finished_at": "2026-11-10T05:54:40Z"},
            {"started_at": "2026-11-10T05:54:40", "finished_at": "2026-11-10T06:03:11"},
        ):
            self.assertIsNone(wall_duration(value))

    def test_exact_metric_uses_actual_state_not_fsq_claim(self):
        case = task()
        self.assertEqual(score_preferences(case, {"profile": {"name": "FSQ Eval"}})["benchmark_score"], 0)
        self.assertEqual(score_preferences(case, {"profile": {"name": "Thomas"}})["benchmark_score"], 1)

    def test_font_range_matches_original_inclusive_min_exclusive_max(self):
        case = task("edge_font_size")
        case["evaluator"]["func"] = "check_edge_font_size"
        case["evaluator"]["expected"]["rules"] = {"type": "range", "min": 24, "max": 99999}
        validate_selected(case)
        for value, expected in ((23, 0), (24, 1), (48, 1), (99999, 0)):
            self.assertEqual(score_preferences(case, {"webkit": {"webprefs": {"default_font_size": value}}})["benchmark_score"], expected)

    def test_download_path_exact_match_does_not_normalize_benchmark(self):
        case = task("edge_default_download_folder")
        case["evaluator"]["expected"]["rules"] = {"expected": "C:\\"}
        self.assertEqual(score_preferences(case, {"download": {"default_directory": "C:\\"}})["benchmark_score"], 1)
        self.assertEqual(score_preferences(case, {"download": {"default_directory": "c:\\"}})["benchmark_score"], 0)

    def test_missing_or_malformed_state_is_not_a_valid_failure_or_success(self):
        with pytest.raises(ValueError, match="lacks required evidence"):
            score_preferences(task(), {})
        case = task("edge_font_size")
        case["evaluator"]["expected"]["rules"] = {"type": "range", "min": 24, "max": 99999}
        with pytest.raises(ValueError, match="not numeric"):
            score_preferences(case, {"webkit": {"webprefs": {"default_font_size": True}}})

    def test_dataset_bytes_and_case_sensitive_identity_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / (DEFAULT_IDS[0] + ".json")
            raw = json.dumps(task(), indent=4).encode()
            path.write_bytes(raw)
            inventory, selected = load_dataset(directory, [DEFAULT_IDS[0]])
            self.assertEqual(path.read_bytes(), raw)
            self.assertEqual(selected[0]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(inventory[0]["definition"]["instruction"], task()["instruction"])
            with pytest.raises(ValueError, match="Unknown"):
                load_dataset(directory, [DEFAULT_IDS[0].upper()])

    def test_unknown_or_unsupported_tasks_are_never_silently_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            value = task("validate_pwa_installed")
            (Path(directory) / (value["id"] + ".json")).write_text(json.dumps(value))
            with pytest.raises(ValueError, match="does not support"):
                load_dataset(directory, [value["id"]])

    def test_dataset_commands_are_allowlisted_data_not_executed_shell(self):
        for command in (["cmd", "/c", "arbitrary"], ["start", "msedge", "--user-data-dir=personal"]):
            case = task()
            case["config"][0]["parameters"]["command"] = command
            with pytest.raises(ValueError, match="not an admitted Edge initialization"):
                validate_selected(case)
        case = task()
        case["evaluator"]["postconfig"] = [{"type": "execute", "parameters": {"command": "something else", "shell": "true"}}]
        with pytest.raises(ValueError, match="not an admitted close/flush/reopen"):
            validate_selected(case)
        valid = task()
        valid["evaluator"]["postconfig"] = [{"type": "execute", "parameters": {"command": "taskkill /IM msedge.exe /F", "shell": "true"}}]
        validate_selected(valid)

    def test_seed_uses_only_new_profile_and_private_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = seed_profile(root / "profile", root / "downloads")
            self.assertEqual(score_preferences(task(), baseline)["benchmark_score"], 0)
            self.assertEqual(baseline["download"]["default_directory"], str(root / "downloads"))
            with pytest.raises(ValueError, match="fresh private Profile"):
                seed_profile(root / "profile", root / "other-downloads")
            self.assertFalse((root / "other-downloads").exists())

    def test_long_root_rejected_before_profile_creation_on_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ("x" * 230)
            self.assertGreater(len(str(path)), 260)
            with pytest.raises(ValueError, match="at most 110"):
                bounded_root(path)
            self.assertFalse(path.exists())
            short = Path(directory) / "literal [test] space"
            short.mkdir()
            (short / "check.json").write_text("{}")
            self.assertEqual((short / "check.json").read_text(), "{}")
            with pytest.raises(ValueError, match="output-marker"):
                bounded_root(short)

    def test_desktop_admission_rejects_overlap_and_releases_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            with pytest.raises(RuntimeError, match="intentional"), desktop_lock(parent):  # noqa: PT012 - Nested admission and failure must happen while the outer lock is held.
                with pytest.raises(RuntimeError, match="Another WAA evaluation"), desktop_lock(parent):
                    self.fail("Concurrent desktop admission")
                raise RuntimeError("intentional")
            with desktop_lock(parent):
                pass

    def test_target_adapter_rejects_non_edge_process_before_installing_patch(self):
        with tempfile.TemporaryDirectory() as directory, pytest.raises(RuntimeError, match="selected Edge executable"):
            install_process_scope(os.getpid(), directory, Path(directory) / "msedge.exe")

    def test_machine_record_mismatch_is_explicit_integration_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def fake_run(command, cwd, out, err, timeout):
                out.write_text(json.dumps({"schema_version": "fsq.machine/v1", "operation": "other", "type": "result", "result": {}}))
                err.write_text("")
                return {"exit_code": 0, "timed_out": False, "terminated_processes": 0}

            with patch("windows_job.run_owned", side_effect=fake_run):
                result = fsq_call(root, root, "check", [], "case.create")
            self.assertEqual(result["protocol_status"], "error")
            self.assertNotIn("record", result)

    def test_timeout_remains_unknown_and_does_not_infer_fsq_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("windows_job.run_owned", return_value={"exit_code": 1, "timed_out": True, "terminated_processes": 2}):
                result = fsq_call(root, root, "timeout", [], "case.create")
            self.assertEqual(result["protocol_status"], "unknown")
            self.assertNotIn("record", result)

    def test_fsq_success_and_benchmark_score_are_independent(self):
        rows = [
            {"fsq_status": "success", "evaluation_status": "valid", "benchmark_score": 0},
            {"fsq_status": "failed", "evaluation_status": "valid", "benchmark_score": 1},
            {"evaluation_status": "error", "benchmark_score": None},
        ]
        summary = summarize(rows)
        self.assertEqual(summary["false_positives"], 1)
        self.assertEqual(summary["benchmark_pass_rate"], 0.5)
        self.assertEqual(summary["unscored"], 1)
        self.assertIsNone(summarize([])["benchmark_pass_rate"])

    def test_provider_401_and_protocol_failures_are_not_benchmark_failures(self):
        observation = {"evaluation_status": "valid", "benchmark_score": 0.0, "observed": "FSQ Eval"}
        response = {
            "protocol_status": "valid",
            "record": {"type": "error", "error": {"category": "configuration", "details": {"status_code": 401}}},
        }
        result = classify_evaluation(response, observation)
        self.assertEqual(result["evaluation_status"], "blocked")
        self.assertIsNone(result["benchmark_score"])
        self.assertEqual(result["observed_state_score"], 0)
        self.assertEqual(observation["evaluation_status"], "valid")
        for protocol, expected in (("unknown", "unknown"), ("error", "error")):
            result = classify_evaluation({"protocol_status": protocol}, observation)
            self.assertEqual(result["evaluation_status"], expected)
            self.assertIsNone(result["benchmark_score"])

    def test_report_escapes_text_embeds_screenshot_and_bundles_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case_root = root / DEFAULT_IDS[0]
            case_root.mkdir()
            (case_root / "before.png").write_bytes(b"synthetic-image")
            report = {"status": "blocked", "tasks": [{"id": DEFAULT_IDS[0], "instruction": "<script>bad</script>"}]}
            write_report(root, report)
            page = (root / "report.html").read_text(encoding="utf-8")
            self.assertNotIn("<script>", page)
            self.assertIn("data:image/png;base64,", page)
            with zipfile.ZipFile(root / "evidence.zip") as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn("report.html", archive.namelist())
                self.assertIn(DEFAULT_IDS[0] + "/before.png", archive.namelist())

    def test_manifest_declares_portable_inputs_and_main_stage_outputs(self):
        manifest = json.loads(Path(__file__).with_name("pipeline.json").read_text())
        self.assertEqual(manifest["platform"], "win32")
        self.assertEqual({value["name"] for value in manifest["variables"]}, {"fsq-python", "dataset-dir", "edge-exe", "case-ids", "git-url", "git-branch"})
        branch = next(value for value in manifest["variables"] if value["name"] == "git-branch")
        self.assertEqual(branch["default"], "user/ty/win-eval-dragon")
        for stage in manifest["stages"]:
            self.assertFalse(stage.get("is_finally", False))
            self.assertEqual(set(stage["marker_policy"]["output"]), {"waa-report", "waa-results", "waa-evidence"})

    def test_private_launch_arguments_do_not_expose_debugging_port_or_personal_profile(self):
        args = edge_arguments(Path("X:\\private-profile"))
        self.assertIn("--user-data-dir=X:\\private-profile", args)
        self.assertFalse(any("remote-debugging" in value for value in args))
        self.assertEqual(args[-1], "about:blank")

    def test_registered_workspace_name_is_unique_across_tasks_and_attempts(self):
        first = workspace_path(Path("X:\\eval\\generation-a\\1"))
        second = workspace_path(Path("X:\\eval\\generation-b\\1"))
        third = workspace_path(Path("X:\\eval\\generation-a\\2"))
        self.assertEqual(len({first.name, second.name, third.name}), 3)
        self.assertNotEqual(first.name, "workspace")

    def test_forced_descendant_cleanup_prevents_persisted_state_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            session = EdgeSession(sys.executable, Path(directory) / "profile")
            job = Mock()
            job.active.return_value = 1
            job.close.return_value = 2
            session.job = job
            with patch("edge_session.time.monotonic", side_effect=[0, 4]), pytest.raises(RuntimeError, match="persisted result is not reliable"):
                session.stop()
            job.close.assert_called_once()
            self.assertIsNone(session.job)

    def test_driver_close_detaches_without_killing_runner_owned_application(self):
        driver = Mock()
        app = Mock()
        driver._app = app
        driver._passed.side_effect = lambda value: value
        result = detach_owned(driver)
        self.assertTrue(result["detached"])
        self.assertIsNone(driver._app)
        app.kill.assert_not_called()

    def test_window_resolution_rejects_foreign_and_hidden_windows(self):
        def enumerate_windows(callback, context):
            for handle in (1, 2, 3, 4):
                callback(handle, context)

        with (
            patch("win32gui.EnumWindows", side_effect=enumerate_windows),
            patch("win32gui.IsWindowVisible", side_effect=lambda handle: handle != 2),
            patch("win32process.GetWindowThreadProcessId", side_effect=lambda handle: (1, 9 if handle == 1 else 42)),
            patch("win32gui.GetClassName", return_value="Chrome_WidgetWin_1"),
            patch("win32gui.GetForegroundWindow", return_value=1),
        ):
            self.assertEqual(visible_owned_handle(42), 3)
            with patch("win32gui.GetForegroundWindow", return_value=4):
                self.assertEqual(visible_owned_handle(42), 4)
        with patch("win32gui.EnumWindows", return_value=None), pytest.raises(RuntimeError, match="no visible"):
            visible_owned_handle(42)


class InvocationTests(unittest.TestCase):
    def test_exact_arguments_and_context_are_persisted_before_process_admission(self):
        goal = "Rename to Thomas's \u2019test\u2019\n$(do-not-run); <script>data</script> | text"
        arguments = ["case", "create", "--platform", "windows", "--goal", goal]
        scope = {"pid": 123, "profile": "X:\\private profile", "executable": "X:\\msedge.exe"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = {"url": "https://github.com/microsoft/FSQ.git", "branch": "main", "commit": "a" * 40, "role": "source_context_only"}
            (root / "git-source.json").write_text(json.dumps(source))

            def execute(command, cwd, out, err, timeout):
                prepared = json.loads((root / "create.command.json").read_text())
                self.assertEqual(prepared["state"], "prepared")
                self.assertEqual(prepared["cli_arguments"], ["--output", "json", "--non-interactive", *arguments])
                self.assertEqual(prepared["wrapper_arguments"], command)
                self.assertEqual(prepared["process_scope"], scope)
                self.assertEqual(prepared["working_directory"], str(root.resolve()))
                self.assertEqual(prepared["case_id"], DEFAULT_IDS[0])
                self.assertEqual(prepared["timeout_seconds"], 540)
                self.assertEqual(prepared["git_source"], source)
                out.write_text(
                    json.dumps(
                        {
                            "schema_version": "fsq.machine/v1",
                            "operation": "case.create",
                            "type": "result",
                            "result": {"status": "success", "run_id": "recorded-run", "report_path": "report.md"},
                        }
                    )
                )
                err.write_text("")
                return {"exit_code": 0, "timed_out": False, "terminated_processes": 0}

            with (
                patch.dict(os.environ, {"FSQ_FAKE_CREDENTIAL": "never-record-this-secret", "DRAGON_TASK_ID": "task-test", "DRAGON_VAR_DATASET_DIR": "X:\\dataset"}),
                patch("windows_job.run_owned", side_effect=execute),
            ):
                response = fsq_call(root, root, "create", arguments, "case.create", scope, 540, DEFAULT_IDS[0])
            record_text = (root / "create.command.json").read_text()
            record = json.loads(record_text)
            self.assertEqual(record["state"], "completed")
            self.assertEqual(record["run_id"], "recorded-run")
            self.assertEqual(record["process"]["exit_code"], 0)
            self.assertEqual(record["task_id"], "task-test")
            self.assertEqual(record["reproduction"]["variables"]["case-ids"], DEFAULT_IDS[0])
            self.assertEqual(record["reproduction"]["variables"]["dataset-dir"], "X:\\dataset")
            self.assertEqual(record["reproduction"]["variables"]["git-url"], source["url"])
            self.assertEqual(record["reproduction"]["variables"]["git-branch"], "main")
            self.assertIn("runner.py", record["package_sha256"])
            self.assertNotIn("never-record-this-secret", record_text)
            self.assertEqual(response["command_record"], "create.command.json")
            page = command_details(root)
            self.assertIn("CLI 等价命令展示", page)
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;", page)
            self.assertIn("旧 PID 已失效", page)

    def test_startup_error_timeout_and_protocol_error_keep_command_records(self):
        for mode, expected in (("startup", "execution_error"), ("timeout", "timed_out"), ("protocol", "protocol_error")):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)

                def execute(command, cwd, out, err, timeout, root=root, mode=mode):
                    self.assertTrue((root / "create.command.json").exists())
                    if mode == "startup":
                        raise OSError("test process launch failed")
                    out.write_text("invalid json" if mode == "protocol" else "")
                    err.write_text("test stderr")
                    return {"exit_code": 1, "timed_out": mode == "timeout", "terminated_processes": 0}

                with patch("windows_job.run_owned", side_effect=execute):
                    if mode == "startup":
                        with pytest.raises(OSError, match="test process launch"):
                            fsq_call(root, root, "create", ["case", "create"], "case.create")
                    else:
                        fsq_call(root, root, "create", ["case", "create"], "case.create")
                record = json.loads((root / "create.command.json").read_text())
                self.assertEqual(record["state"], expected)
                self.assertIn("finished_at", record)
                self.assertEqual(record["cli_arguments"][-2:], ["case", "create"])

    def test_failed_cli_result_keeps_exit_code_and_original_error_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def execute(command, cwd, out, err, timeout):
                out.write_text(
                    json.dumps(
                        {
                            "schema_version": "fsq.machine/v1",
                            "operation": "doctor",
                            "type": "error",
                            "error": {"code": "configuration.invalid", "category": "configuration"},
                        }
                    )
                )
                err.write_text("")
                return {"exit_code": 3, "timed_out": False, "terminated_processes": 0}

            with patch("windows_job.run_owned", side_effect=execute):
                fsq_call(root, root, "doctor", ["doctor"], "doctor")
            record = json.loads((root / "doctor.command.json").read_text())
            self.assertEqual(record["state"], "failed")
            self.assertEqual(record["process"]["exit_code"], 3)
            self.assertEqual(record["fsq_error_code"], "configuration.invalid")

    def test_existing_records_and_credential_operations_are_rejected_before_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = '{"state":"prepared"}'
            (root / "doctor.command.json").write_text(original)
            with patch("windows_job.run_owned") as execute:
                with pytest.raises(FileExistsError):
                    fsq_call(root, root, "doctor", ["doctor"], "doctor")
                with pytest.raises(ValueError, match="authentication commands are excluded"):
                    fsq_call(root, root, "auth", ["providers", "configure"], "providers.configure")
                with pytest.raises(ValueError, match="Credential arguments"):
                    fsq_call(root, root, "create", ["case", "create", "--api-key=secret"], "case.create")
                execute.assert_not_called()
            self.assertEqual((root / "doctor.command.json").read_text(), original)
            self.assertFalse((root / "auth.command.json").exists())
            self.assertFalse((root / "create.command.json").exists())

    def test_record_update_failure_preserves_the_original_process_exception(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch("windows_job.run_owned", side_effect=OSError("original startup failure")),
                patch("invocation.finish_invocation", side_effect=OSError("record storage unavailable")),
                pytest.raises(OSError, match="original startup failure") as error,
            ):
                fsq_call(root, root, "doctor", ["doctor"], "doctor")
            self.assertIn("outcome could not be saved", " ".join(error.value.__notes__))
            self.assertEqual(json.loads((root / "doctor.command.json").read_text())["state"], "prepared")

    def test_history_without_commands_is_explicit_and_corrupt_records_do_not_hide_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertIn("未根据源码推测或补造命令", command_details(root))
            (root / "create.command.json").write_text("invalid")
            self.assertIn("无法读取调用记录", command_details(root))


if __name__ == "__main__":
    unittest.main()
