# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Protects complete remote packaging, source-runtime identity checks, and safe artifact extraction.
import base64
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from build_remote import FILES, build
from portable_bootstrap import extract_snapshot
from runtime_contract import driver_digest, verify_runtime


class PortableTests(unittest.TestCase):
    def test_build_embeds_every_runtime_asset_and_byte_identical_dataset_without_machine_paths(self):
        package = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            raw = b'{"id":"test","instruction":"original"}\n'
            (dataset / "test.json").write_bytes(raw)
            output = root / "output"
            manifest = build(package, dataset, output)
            script = (output / "deploy.ps1").read_text(encoding="utf-8")
            payload_text = script.split("[Convert]::FromBase64String('", 1)[1].split("')", 1)[0]
            payload = base64.b64decode(payload_text)
            self.assertIn(hashlib.sha256(payload).hexdigest(), script)
            self.assertNotIn(str(dataset), script)
            self.assertNotIn(str(package), script)
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertEqual(set(archive.namelist()), {*FILES, "dataset/test.json", "package-manifest.json"})
                self.assertEqual(archive.read("dataset/test.json"), raw)
                for name, digest in manifest["files"].items():
                    self.assertEqual(hashlib.sha256(archive.read(name)).hexdigest(), digest)
                self.assertNotIn("provider-token.json", archive.namelist())
            previous = (output / "deploy.ps1").read_bytes()
            build(package, dataset, output)
            self.assertEqual(previous, (output / "deploy.ps1").read_bytes())
            setup_output = root / "setup-output"
            build(package, dataset, setup_output, mode="setup")
            setup_script = (setup_output / "deploy.ps1").read_text(encoding="utf-8")
            self.assertIn("$env:DRAGON_VAR_OPERATION_MODE = 'setup'", setup_script)
            self.assertNotIn("__OPERATION_MODE__", setup_script)

    def test_archive_rejects_escape_links_and_long_paths_before_materializing_entries(self):
        for name, mode in (("../outside.txt", 0), ("C:/outside.txt", 0), ("bad\\outside.txt", 0), ("link", 0o120777 << 16), ("x" * 230 + "/entry.txt", 0)):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / "source.zip"
                with zipfile.ZipFile(archive, "w") as bundle:
                    info = zipfile.ZipInfo(name)
                    info.external_attr = mode
                    bundle.writestr(info, "bad")
                if "\\" in name:
                    archive.write_bytes(archive.read_bytes().replace(name.replace("\\", "/").encode(), name.encode()))
                with pytest.raises(ValueError, match=r"source|archive|Archive|Source"):
                    extract_snapshot(archive, root / "source")
                self.assertFalse(any((root / "source").iterdir()))
                self.assertFalse((root / "outside.txt").exists())

    def test_owned_source_snapshot_can_be_extracted_and_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "source.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("fsq_agent/entry.py", "print('source')")
            extract_snapshot(archive, root / "source")
            self.assertEqual((root / "source" / "fsq_agent" / "entry.py").read_text(), "print('source')")

    def test_driver_fingerprint_is_stable_across_checkout_and_archive_line_endings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "lf.py").write_bytes(b"first\nsecond\n")
            (root / "crlf.py").write_bytes(b"first\r\nsecond\r\n")
            self.assertEqual(driver_digest(root / "lf.py"), driver_digest(root / "crlf.py"))

    def test_unsupported_runtime_version_does_not_silently_run(self):
        with patch("runtime_contract.importlib.metadata.version", return_value="99.0.0"), pytest.raises(RuntimeError, match="not an inspected"):
            verify_runtime()

    def test_unavailable_screen_capture_blocks_instead_of_reporting_a_case_failure(self):
        from edge_session import require_screen_capture

        with patch("PIL.ImageGrab.grab", side_effect=OSError("screen grab failed")), pytest.raises(RuntimeError, match="Connect to the executing Dev Box"):
            require_screen_capture()
        with patch("PIL.ImageGrab.grab") as grab:
            require_screen_capture()
            grab.return_value.close.assert_called_once()

    def test_remote_report_distinguishes_execution_machine_and_installed_source_from_local_baseline(self):
        from reporting import render_report

        report = {
            "status": "ready",
            "tasks": [],
            "execution_machine": "REMOTE-HOST",
            "git_source": {"role": "installed_runtime_source", "url": "https://github.com/microsoft/FSQ.git", "branch": "user/ty/win-eval-dragon", "commit": "a" * 40},
        }
        with tempfile.TemporaryDirectory() as directory:
            page = render_report(Path(directory), report)
        self.assertIn("<h1>FSQ 远程评测报告</h1>", page)
        self.assertIn("本轮远程成功率", page)
        self.assertIn("REMOTE-HOST", page)
        self.assertIn("本次 FSQ 从此提交的归档安装到远端独立虚拟环境", page)
        self.assertNotIn("此处记录远程代码上下文", page)

    def test_no_local_source_or_dataset_paths_required_by_remote_manifest(self):
        remote = Path(__file__).parent.parent / "fsq-waa-remote-eval" / "pipeline.json"
        manifest = json.loads(remote.read_text())
        variables = {entry["name"]: entry for entry in manifest["variables"]}
        self.assertNotIn("fsq-python", variables)
        self.assertNotIn("dataset-dir", variables)
        self.assertEqual(variables["git-branch"]["default"], "user/ty/win-eval-dragon")
        self.assertEqual(len(manifest["stages"]), 1)

    def test_shipped_remote_stage_contains_current_maintained_runtime_files(self):
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from build import build as build_bundle

        package = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            build_bundle(output)
            script = (output / "pipelines/fsq-waa-remote-eval/deploy.ps1").read_text(encoding="utf-8")
        payload = base64.b64decode(script.split("[Convert]::FromBase64String('", 1)[1].split("')", 1)[0])
        self.assertIn(hashlib.sha256(payload).hexdigest(), script)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for name in FILES:
                self.assertEqual(archive.read(name), (package / name).read_bytes(), f"Rebuild remote payload: {name}")
            self.assertEqual(sum(name.startswith("dataset/") and name.endswith(".json") for name in archive.namelist()), 13)

    def test_native_tool_informational_stderr_is_retained_without_leaking_to_stage_stderr(self):
        import shutil
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            program = root / "native.py"
            program.write_text("import sys\nprint('already installed', file=sys.stderr)\n")
            script = root / "test.ps1"
            script.write_text(
                "param([string]$Python, [string]$Root)\n"
                "$ErrorActionPreference='Stop'\n"
                "& $Python (Join-Path $Root 'native.py') 1> (Join-Path $Root 'stdout.log') 2> (Join-Path $Root 'stderr.log')\n"
                "if ($LASTEXITCODE -ne 0) { throw 'native command failed' }\n"
                "Write-Output 'complete'\n",
                encoding="utf-8",
            )
            result = subprocess.run([shutil.which("pwsh"), "-NoProfile", "-File", str(script), "-Python", sys.executable, "-Root", str(root)], capture_output=True, text=True, timeout=30, check=False)  # noqa: S603 - Execute only the temporary informational-stderr fixture script.
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            self.assertIn("already installed", (root / "stderr.log").read_text())


if __name__ == "__main__":
    unittest.main()
