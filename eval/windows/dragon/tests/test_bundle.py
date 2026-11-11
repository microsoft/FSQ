# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
"""Offline contract tests. No Dragon commands or evaluation processes run here."""

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build
import install
import pytest
from _bundle import PIPELINES, REPOSITORY, SOURCE_FILES, inventory, publish_new


class BundleTests(TestCase):
    def setUp(self):
        import tempfile

        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / "package"
        self.target = self.root / "dragon-data"

    def bundle(self):
        build.build(self.package)
        return self.package

    def test_relocated_toolkit_uses_sibling_dataset_and_repository_output_boundary(self):
        self.assertEqual(ROOT.parent, REPOSITORY / "eval" / "windows")
        self.assertTrue((REPOSITORY / "pyproject.toml").is_file())
        self.assertEqual(build.DATASET, ROOT.parent / "examples" / "msedge")

    def test_complete_deterministic_payloads_and_dataset(self):
        first = self.bundle()
        second = self.root / "second"
        build.build(second)
        self.assertEqual(inventory(first), inventory(second))
        pin = json.loads((ROOT / "dataset-manifest.json").read_bytes())["files"]
        self.assertEqual(len(pin), 13)
        for name, digest in pin.items():
            data = (first / "dataset/msedge" / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        for pipeline, mode in (("fsq-dev-box-setup", "setup"), ("fsq-waa-remote-eval", "evaluate")):
            directory = first / "pipelines" / pipeline
            script = (directory / "deploy.ps1").read_text(encoding="utf-8")
            self.assertIn(f"$env:DRAGON_VAR_OPERATION_MODE = '{mode}'", script)
            encoded = script.split("[Convert]::FromBase64String('", 1)[1].split("')", 1)[0]
            payload = base64.b64decode(encoded, validate=True)
            self.assertIn(hashlib.sha256(payload).hexdigest(), script)
            manifest = json.loads((directory / "payload-manifest.json").read_bytes())
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                self.assertTrue(all(info.date_time == (2026, 1, 1, 0, 0, 0) for info in archive.infolist()))
                self.assertTrue(all(info.create_system == 3 for info in archive.infolist()))
                self.assertEqual(json.loads(archive.read("package-manifest.json")), manifest)
                self.assertEqual(set(archive.namelist()), {*manifest["files"], "package-manifest.json"})
                for name, digest in manifest["files"].items():
                    data = archive.read(name)
                    self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
                    expected = first / "dataset/msedge" / name.removeprefix("dataset/") if name.startswith("dataset/") else first / "pipelines/fsq-waa-edge-eval" / name
                    self.assertEqual(data, expected.read_bytes())
        self.assertEqual({p.name for p in (first / "pipelines").iterdir()}, set(PIPELINES))
        build.build(first)  # Identical output is not rewritten.
        before = (first / "README.md").stat().st_mtime_ns
        build.build(first)
        self.assertEqual(before, (first / "README.md").stat().st_mtime_ns)
        (first / "README.md").write_text("different")
        with pytest.raises(ValueError, match="conflict"):
            build.build(first)

    def test_dataset_changed_missing_extra_and_ambiguous_are_rejected(self):
        dataset = self.root / "dataset"
        shutil.copytree(build.DATASET, dataset)
        name = next(dataset.glob("*.json"))
        original = name.read_bytes()
        for change in ("changed", "missing", "extra", "case"):
            with self.subTest(change=change):
                name.write_bytes(original)
                if change == "changed":
                    name.write_bytes(original + b" ")
                elif change == "missing":
                    name.unlink()
                elif change == "extra":
                    (dataset / "extra.json").write_text("{}")
                else:
                    name.rename(name.with_suffix(".JSON"))
                with patch.object(build, "DATASET", dataset), pytest.raises(ValueError, match="Dataset"):
                    build.build(self.package)
                self.assertFalse(self.package.exists())
                if change == "extra":
                    (dataset / "extra.json").unlink()
                elif change == "case":
                    name.with_suffix(".JSON").rename(name)

    def test_input_allowlist_excludes_machine_state_and_generated_sources(self):
        source = self.root / "source"
        shutil.copytree(ROOT / "pipelines", source, ignore=shutil.ignore_patterns("__pycache__"))
        local = source / "fsq-waa-edge-eval"
        for name in (".env", ".npmrc", ".dragon-runtime.json", "secret.json", "evidence.zip"):
            (local / name).write_text("must not ship")
        (source / "fsq-dev-box-setup/deploy.ps1").write_text("stale generated")
        with patch.object(build, "SOURCES", source):
            self.bundle()
        self.assertFalse(any("secret" in name or ".env" in name or ".dragon-runtime.json" in name for name in inventory(self.package)))
        self.assertNotIn(b"stale generated", (self.package / "pipelines/fsq-dev-box-setup/deploy.ps1").read_bytes())
        (local / SOURCE_FILES["fsq-waa-edge-eval"][0]).unlink()
        with patch.object(build, "SOURCES", source), pytest.raises((ValueError, OSError)):
            build.build(self.root / "missing")

    def test_install_new_idempotent_and_runtime_metadata_is_never_read(self):
        self.bundle()
        install.install(self.package, self.target)
        before = inventory(self.target)
        metadata = self.target / "pipelines/fsq-waa-edge-eval/.dragon-runtime.json"
        metadata.write_bytes(b"not even valid JSON: secret")
        old_stat = metadata.stat()
        read_bytes = Path.read_bytes

        def guarded(path):
            if path.name == ".dragon-runtime.json":
                self.fail("runtime metadata was read")
            return read_bytes(path)

        with patch.object(Path, "read_bytes", guarded):
            install.install(self.package, self.target)
        self.assertEqual(metadata.stat().st_mtime_ns, old_stat.st_mtime_ns)
        self.assertEqual(metadata.read_bytes(), b"not even valid JSON: secret")
        metadata.unlink()
        self.assertEqual(inventory(self.target), before)

    def test_whole_batch_conflicts_before_any_writes(self):
        self.bundle()
        conflict = self.target / "pipelines" / PIPELINES[-1]
        shutil.copytree(self.package / "pipelines" / PIPELINES[-1], conflict)
        sample = conflict / "pipeline.json"
        original = sample.read_bytes()
        for change in ("changed", "missing", "extra", "empty-directory"):
            with self.subTest(change=change):
                sample.write_bytes(original)
                if change == "changed":
                    sample.write_bytes(b"changed")
                elif change == "missing":
                    sample.unlink()
                elif change == "extra":
                    (conflict / "unknown").write_text("preserve")
                else:
                    (conflict / "empty").mkdir()
                before = inventory(self.target)
                with pytest.raises(ValueError, match="conflict"):
                    install.install(self.package, self.target)
                self.assertEqual(inventory(self.target), before)
                self.assertEqual([p.name for p in conflict.parent.iterdir()], [PIPELINES[-1]])
                if change == "extra":
                    (conflict / "unknown").unlink()
                elif change == "empty-directory":
                    (conflict / "empty").rmdir()

    def test_inventory_tampering_and_path_attacks(self):
        self.bundle()
        manifest_path = self.package / "bundle-manifest.json"
        original = manifest_path.read_bytes()
        value = json.loads(original)
        for name in ("../escape", "/absolute", "C:/absolute", "pipelines\\bad", "pipelines/../escape", "pipelines/CON", "pipelines/name.", "pipelines/a:b"):
            with self.subTest(name=name):
                candidate = {**value, "files": {**value["files"], name: "0" * 64}}
                manifest_path.write_text(json.dumps(candidate))
                with pytest.raises(ValueError, match="relative path"):
                    install.install(self.package, self.target)
                self.assertFalse(self.target.exists())
        manifest_path.write_bytes(original)
        (self.package / "unknown").write_text("extra")
        with pytest.raises(ValueError, match="inventory conflict"):
            install.install(self.package, self.target)
        (self.package / "unknown").unlink()
        (self.package / "README.md").write_text("tampered")
        with pytest.raises(ValueError, match="hash"):
            install.install(self.package, self.target)
        self.assertFalse(self.target.exists())

    def test_overlapping_and_maintained_outputs_rejected(self):
        self.bundle()
        for target in (self.package, self.package / "nested", self.root, ROOT, ROOT / "pipelines/new"):
            with self.subTest(target=target), pytest.raises(ValueError, match="overlap"):
                install.install(self.package, target)
        for output in (ROOT / "pipelines/new", REPOSITORY / "eval/new", REPOSITORY, self.root / "missing/../package"):
            with self.subTest(output=output), pytest.raises(ValueError, match=r"Output|Traversal"):
                build.build(output)

    def test_junction_or_symlink_inputs_and_targets_rejected(self):
        self.bundle()
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        if sys.platform == "win32":
            result = subprocess.run([os.environ["COMSPEC"], "/c", "mklink", "/J", str(link), str(real)], capture_output=True, check=False)  # noqa: S603 - Fixed mklink fixture argv creates only a temporary junction.
            self.assertEqual(result.returncode, 0, result.stderr)
        else:
            link.symlink_to(real, target_is_directory=True)
        self.addCleanup(link.rmdir if sys.platform == "win32" else link.unlink)
        with pytest.raises(ValueError, match="reparse"):
            install.install(self.package, link / "data")
        (self.package / "extra").mkdir()
        nested = self.package / "extra/link"
        if sys.platform == "win32":
            subprocess.run([os.environ["COMSPEC"], "/c", "mklink", "/J", str(nested), str(real)], capture_output=True, check=True)  # noqa: S603 - Fixed mklink fixture argv creates only a temporary junction.
        else:
            nested.symlink_to(real, target_is_directory=True)
        self.addCleanup(nested.rmdir if sys.platform == "win32" else nested.unlink)
        with pytest.raises(ValueError, match="reparse"):
            install.install(self.package, self.target)
        self.assertFalse(self.target.exists())

    def test_later_commit_failure_rolls_back_only_new_entries(self):
        self.bundle()
        existing = self.target / "pipelines" / PIPELINES[0]
        shutil.copytree(self.package / "pipelines" / PIPELINES[0], existing)
        before = inventory(self.target)
        calls = []

        def failing(destination, files):
            calls.append(destination)
            if len(calls) == 2:
                raise OSError("injected later failure")
            return publish_new(destination, files)

        with patch.object(install, "publish_new", failing), pytest.raises(OSError, match="injected"):
            install.install(self.package, self.target)
        self.assertEqual(inventory(self.target), before)
        self.assertEqual([p.name for p in existing.parent.iterdir()], [PIPELINES[0]])

    def test_partial_file_write_is_rolled_back(self):
        destination = self.root / "partial"
        original_open = Path.open

        class FailingStream:
            def __init__(self, path):
                self.stream = original_open(path, "xb")

            def __enter__(self):
                return self

            def write(self, data):
                self.stream.write(data[:2])
                raise OSError("injected partial write")

            def __exit__(self, *args):
                self.stream.close()

        def faulty_open(path, mode="r", *args, **kwargs):
            if path.parent == destination and mode == "xb":
                return FailingStream(path)
            return original_open(path, mode, *args, **kwargs)

        with patch.object(Path, "open", faulty_open), pytest.raises(OSError, match="injected partial"):
            publish_new(destination, {"file.txt": b"complete"})
        self.assertFalse(destination.exists())

    def test_hardlinked_package_file_is_rejected_without_target_writes(self):
        self.bundle()
        original = self.root / "linked-guide"
        guide = self.package / "README.md"
        guide.rename(original)
        guide.hardlink_to(original)
        with pytest.raises(ValueError, match=r"links|Links"):
            install.install(self.package, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(guide.read_bytes(), original.read_bytes())

    def test_legacy_payload_builder_also_rejects_source_outputs_and_differences(self):
        generate = build.runtime_builder()
        local = ROOT / "pipelines/fsq-waa-edge-eval"
        dataset = build.DATASET
        output = self.root / "payload"
        generate(local, dataset, output)
        generate(local, dataset, output)
        (output / "deploy.ps1").write_text("preserve")
        with pytest.raises(ValueError, match="conflict"):
            generate(local, dataset, output)
        self.assertEqual((output / "deploy.ps1").read_text(), "preserve")
        with pytest.raises(ValueError, match="Output"):
            generate(local, dataset, ROOT / "pipelines/fsq-dev-box-setup")

    def test_cli_errors_are_nonzero_stderr_and_have_no_overwrite_option(self):
        for script, arguments in (
            ("build.py", ["--output", str(ROOT / "pipelines/bad")]),
            ("install.py", ["--package", str(self.root / "missing"), "--dragon-data-dir", str(self.target)]),
            ("install.py", ["--force"]),
        ):
            result = subprocess.run([sys.executable, str(ROOT / script), *arguments], capture_output=True, text=True, check=False)  # noqa: S603 - Test only the maintained offline CLI with fixed rejection inputs.
            self.assertNotEqual(result.returncode, 0)
            self.assertTrue(result.stderr)
            self.assertNotIn("installed", result.stdout)
