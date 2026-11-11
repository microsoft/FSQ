# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Verifies first-clone/reuse synchronization, preservation, and real Windows Git path handling.
import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from git_source import source_inputs, sync_checkout
from locks import file_lock

URL = "https://github.com/microsoft/FSQ.git"


class GitSourceTests(unittest.TestCase):
    def test_source_url_requires_exact_https_github_repository_without_credentials(self):
        self.assertEqual(source_inputs(URL, "main"), (URL, "main"))
        self.assertEqual(source_inputs("https://github.com/microsoft/FSQ", "feature/test"), (URL, "feature/test"))
        for value in (
            "http://github.com/microsoft/FSQ",
            "https://user:secret@github.com/microsoft/FSQ",
            "https://evil.example/microsoft/FSQ",
            URL + "?token=secret",
            URL + "#fragment",
            "file:///C:/Users/user/code/FSQ",
            "https://github.com/owner/../repo",
        ):
            with self.subTest(value=value), pytest.raises(ValueError, match="git-url"):
                source_inputs(value, "main")
        for branch in ("", "-main", "/main", "main/", "main\nother"):
            with self.subTest(branch=branch), pytest.raises(ValueError, match="branch"):
                source_inputs(URL, branch)

    def test_unknown_cache_identity_and_live_sync_lock_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            key = hashlib.sha256((URL.casefold() + "\nmain").encode()).hexdigest()[:16]
            container = root / key
            container.mkdir()
            owner = container / "owner.json"
            original = '{"url":"different","branch":"main"}'
            owner.write_text(original)
            execute = Mock()
            with pytest.raises(ValueError, match="identity differs"):
                sync_checkout(URL, "main", root, execute)
            self.assertEqual(owner.read_text(), original)
            execute.assert_not_called()
            with file_lock(container / "sync.lock", "busy"), pytest.raises(RuntimeError, match="Another source synchronization"):
                sync_checkout(URL, "main", root, execute)
            execute.assert_not_called()

    def test_long_managed_cache_root_is_rejected_before_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / ("x" * 230)
            execute = Mock()
            with pytest.raises(ValueError, match="at most 110"):
                sync_checkout(URL, "main", root, execute)
            self.assertFalse(root.exists())
            execute.assert_not_called()

    def test_source_stage_failure_publishes_blocker_without_trying_git_or_old_source(self):
        from git_source import main
        from reporting import save_json

        with tempfile.TemporaryDirectory(prefix="waa-source-") as directory:
            root = Path(directory) / "waa-eval"
            root.mkdir()
            save_json(root / "results.json", {"status": "ready", "tasks": [], "errors": []})
            with (
                patch.dict("os.environ", {"DRAGON_TASK_WORK_DIR": directory, "DRAGON_VAR_GIT_URL": "https://unapproved.example/repo", "DRAGON_VAR_GIT_BRANCH": "main"}),
                patch("sys.argv", ["git_source.py"]),
                patch("git_source.GitCommands") as execute,
            ):
                self.assertEqual(main(), 1)
                execute.assert_not_called()
            report = json.loads((root / "results.json").read_text())
            self.assertEqual(report["status"], "blocked")
            self.assertTrue(report["errors"])
            self.assertTrue((root / "report.html").is_file())
            self.assertTrue((root / "evidence.zip").is_file())
            self.assertFalse((root / "git-source.json").exists())

    def test_evaluation_requires_a_matching_resolved_source_before_any_ui(self):
        import importlib.metadata

        from reporting import save_json
        from runner import run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = {"status": "ready", "fsq_version": importlib.metadata.version("fsq-agent")}
            with pytest.raises(RuntimeError, match="synchronization did not complete"):
                run(root, report)
            save_json(root / "git-source.json", {"url": URL, "branch": "old-branch"})
            with patch.dict("os.environ", {"DRAGON_VAR_GIT_URL": URL, "DRAGON_VAR_GIT_BRANCH": "main"}), pytest.raises(RuntimeError, match="inputs changed"):
                run(root, report)

    def test_missing_branch_does_not_silently_sync_main(self):
        import os

        from git_source import main
        from reporting import save_json

        with tempfile.TemporaryDirectory(prefix="waa-branch-") as directory:
            root = Path(directory) / "waa-eval"
            root.mkdir()
            save_json(root / "results.json", {"status": "ready", "tasks": [], "errors": []})
            with patch.dict(os.environ, {"DRAGON_TASK_WORK_DIR": directory, "DRAGON_VAR_GIT_URL": URL}), patch("sys.argv", ["git_source.py"]), patch("git_source.GitCommands") as execute:
                os.environ.pop("DRAGON_VAR_GIT_BRANCH", None)
                self.assertEqual(main(), 1)
                execute.assert_not_called()
            self.assertFalse((root / "git-source.json").exists())

    def test_real_git_first_clone_reuse_remote_advance_dirty_guard_and_long_paths(self):
        executable = shutil.which("git")
        self.assertIsNotNone(executable)
        with tempfile.TemporaryDirectory(prefix="waa-git-") as directory:
            root = Path(directory)
            upstream, hooks = root / "upstream", root / "hooks"
            upstream.mkdir()
            hooks.mkdir()
            base = [executable, "-c", "core.longpaths=true", "-c", "core.fsmonitor=false", "-c", f"core.hooksPath={hooks}", "-c", f"init.templateDir={hooks}"]

            def upstream_git(*arguments):
                result = subprocess.run([*base, *arguments], cwd=upstream, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603 - Local fixture Git argv, no remote access or shell.
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            upstream_git("init", "--initial-branch=main")
            upstream_git("config", "user.name", "WAA test")
            upstream_git("config", "user.email", "waa@example.test")
            (upstream / "version.txt").write_text("one")
            long_relative = Path("long") / ("x" * 100) / ("y" * 100) / "evidence.txt"
            long_source = upstream / long_relative
            self.assertGreater(len(str(long_source)), 260)
            long_source.parent.mkdir(parents=True)
            long_source.write_text("long-path-evidence")
            upstream_git("add", ".")
            upstream_git("commit", "-m", "first")
            first_commit = upstream_git("rev-parse", "HEAD")
            calls = []

            def execute(arguments, cwd):
                calls.append(arguments)
                # Redirect only this fixed test URL to the owned local upstream; never access the network.
                result = subprocess.run(  # noqa: S603 - Local fixture Git argv; status is checked explicitly below.
                    [*base, "-c", f"url.{upstream}.insteadOf={URL}", *arguments],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(result.stderr)
                return result.stdout.strip()

            first = sync_checkout(URL, "main", root / "cache", execute)
            self.assertEqual(first["action"], "cloned_and_fetched")
            self.assertEqual(first["commit"], first_commit)
            checkout = Path(first["checkout_directory"])
            self.assertGreater(len(str(checkout / long_relative)), 260)
            self.assertEqual((checkout / long_relative).read_text(), "long-path-evidence")
            (upstream / "version.txt").write_text("two")
            upstream_git("add", "version.txt")
            upstream_git("commit", "-m", "second")
            second = sync_checkout(URL, "main", root / "cache", execute)
            self.assertEqual(second["action"], "reused_and_fetched")
            self.assertEqual(second["checkout_directory"], first["checkout_directory"])
            self.assertNotEqual(second["commit"], first["commit"])
            self.assertEqual(second["commit"], upstream_git("rev-parse", "HEAD"))
            self.assertEqual((checkout / "version.txt").read_text(), "two")
            self.assertEqual(sum(call[0] == "clone" for call in calls), 1)
            user_file = checkout / "keep-user-change.txt"
            user_file.write_text("preserve")
            fetches = sum(call[0] == "fetch" for call in calls)
            with pytest.raises(ValueError, match="local changes"):
                sync_checkout(URL, "main", root / "cache", execute)
            self.assertEqual(user_file.read_text(), "preserve")
            self.assertEqual(sum(call[0] == "fetch" for call in calls), fetches)
            subprocess.run([*base, "remote", "set-url", "origin", "https://github.com/other/repo.git"], cwd=checkout, capture_output=True, check=True, timeout=30)  # noqa: S603 - Only edit local fixture config; never contact this URL.
            with pytest.raises(ValueError, match="origin differs"):
                sync_checkout(URL, "main", root / "cache", execute)
            self.assertEqual(user_file.read_text(), "preserve")
            negative = root / "negative"
            subprocess.run([*base, "clone", "--no-checkout", "--no-hardlinks", str(upstream), str(negative)], capture_output=True, check=True, timeout=30)  # noqa: S603 - Clone a temporary local fixture only.
            result = subprocess.run([*base, "-c", "core.longpaths=false", "checkout", "--detach", second["commit"]], cwd=negative, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603 - Local fixture checkout, no shell.
            self.assertNotEqual(result.returncode, 0, "Negative control did not expose the native Windows path boundary.")
            # Every Git process is settled before TemporaryDirectory reclaims the owned long-path trees.
            self.assertTrue(long_source.exists())


if __name__ == "__main__":
    unittest.main()
