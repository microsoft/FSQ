# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# Resolves a configured GitHub branch into a dedicated, reusable source checkout without touching user repositories.
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

from edge_session import bounded_root
from locks import file_lock
from reporting import save_json, write_report


def source_inputs(url, branch):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment or parts.username or parts.password:
        raise ValueError("git-url must be a credential-free HTTPS github.com repository URL.")
    components = parts.path.strip("/").split("/")
    if len(components) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", value) for value in components):
        raise ValueError("git-url must name exactly one GitHub owner/repository.")
    owner, repository = components
    repository = repository.removesuffix(".git")
    if owner in {".", ".."} or repository in {"", ".", ".."}:
        raise ValueError("git-url does not name a repository.")
    if not branch or branch.startswith(("-", "/")) or branch.endswith("/") or any(char.isspace() or ord(char) < 32 for char in branch):
        raise ValueError("git-branch must be a named remote branch, not an option or expression.")
    return f"https://github.com/{owner}/{repository}.git", branch


class GitCommands:
    def __init__(self, logs, hooks):
        executable = shutil.which("git")
        if not executable:
            raise RuntimeError("Git is unavailable on this Machine. Install Git before running source synchronization.")
        self.executable = executable
        self.logs = logs
        self.hooks = hooks
        self.sequence = 0
        self.deadline = time.monotonic() + 360
        logs.mkdir(parents=True, exist_ok=True)
        hooks.mkdir(parents=True, exist_ok=True)
        if any(hooks.iterdir()):
            raise RuntimeError("The package's disabled-hooks directory is not empty. Source synchronization stopped.")

    def __call__(self, arguments, cwd):
        from windows_job import run_owned

        self.sequence += 1
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("The Git synchronization budget expired before another command could start.")
        label = f"{self.sequence:02d}"
        command = [
            self.executable,
            "-c",
            "core.longpaths=true",
            "-c",
            "core.fsmonitor=false",
            "-c",
            f"core.hooksPath={self.hooks}",
            "-c",
            f"init.templateDir={self.hooks}",
            *arguments,
        ]
        stdout, stderr = self.logs / f"{label}.stdout.log", self.logs / f"{label}.stderr.log"
        wrapper = [sys.executable, "-u", str(Path(__file__).resolve()), "git-invoke", *command]
        save_json(self.logs / f"{label}.command.json", {"arguments": command, "cwd": str(cwd)})
        process = run_owned(wrapper, cwd, stdout, stderr, min(remaining, 180 if arguments[0] == "clone" else 60))
        save_json(self.logs / f"{label}.process.json", process)
        if process["timed_out"]:
            raise TimeoutError(f"Git {arguments[0]} timed out; its process tree was stopped. Inspect {stderr}.")
        if process["exit_code"] != 0:
            raise RuntimeError(f"Git {arguments[0]} failed with exit {process['exit_code']}. Inspect {stderr}.")
        return stdout.read_text(encoding="utf-8-sig").strip()


def reject_link(path):
    if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
        raise ValueError("Managed source directories must not be symbolic links or junctions.")


def sync_checkout(url, branch, cache_root, git):
    url, branch = source_inputs(url, branch)
    cache_root = bounded_root(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256((url.casefold() + "\n" + branch).encode()).hexdigest()[:16]
    container = bounded_root(cache_root / key)
    reject_link(container)
    container.mkdir(exist_ok=True)
    identity = {"url": url, "branch": branch}
    with file_lock(container / "sync.lock", "Another source synchronization owns this checkout. Wait for it to finish."):
        owner = container / "owner.json"
        if owner.exists():
            saved = json.loads(owner.read_text(encoding="utf-8"))
            if saved != identity:
                raise ValueError("The managed source identity differs from this request. No Git update was attempted.")
        else:
            if any(path.name != "sync.lock" for path in container.iterdir()):
                raise ValueError("An unowned source cache already exists. It will not be overwritten.")
            save_json(owner, identity)
        checkout = container / "checkout"
        reject_link(checkout)
        git(["check-ref-format", "--branch", branch], container)
        cloned = not checkout.exists()
        if cloned:
            staging = container / ("partial-" + uuid.uuid4().hex[:8])
            git(["clone", "--no-tags", "--single-branch", "--branch", branch, "--", url, str(staging)], container)
            staging.rename(checkout)
        reject_link(checkout / ".git")
        if not (checkout / ".git").is_dir():
            raise ValueError("The managed source must own a normal .git directory, not refer to an external Git directory.")
        if Path(git(["rev-parse", "--show-toplevel"], checkout)).resolve() != checkout.resolve():
            raise ValueError("The managed checkout resolves to another repository.")
        origin = git(["config", "--get", "remote.origin.url"], checkout)
        if origin != url:
            raise ValueError("The managed checkout's origin differs from git-url. It will not be updated.")
        if git(["status", "--porcelain", "--untracked-files=all"], checkout):
            raise ValueError("The dedicated source checkout has local changes. They were preserved; no fetch or reset was attempted.")
        reference = f"refs/remotes/origin/{branch}"
        git(["fetch", "--no-tags", "--no-recurse-submodules", "origin", f"+refs/heads/{branch}:{reference}"], checkout)
        commit = git(["rev-parse", "--verify", reference + "^{commit}"], checkout)
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise ValueError("The remote branch did not resolve to a valid commit identity.")
        git(["checkout", "--detach", commit], checkout)
        if git(["rev-parse", "HEAD"], checkout) != commit:
            raise ValueError("Source checkout verification failed.")
        if git(["status", "--porcelain", "--untracked-files=all"], checkout):
            raise ValueError("The source checkout is not clean after checkout. Evaluation was not started.")
        return {
            **identity,
            "commit": commit,
            "checkout_directory": str(checkout),
            "action": "cloned_and_fetched" if cloned else "reused_and_fetched",
            "resolved_at": datetime.now(UTC).isoformat(),
            "role": "source_context_only",
            "runtime_note": "FSQ execution still uses fsq-python; this source checkout is not installed or executed.",
        }


def main():
    if sys.argv[1:] and sys.argv[1] == "git-invoke":
        if sys.stdin.buffer.read(1) != b"1":
            raise RuntimeError("Git process admission was not granted.")
        environment = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
        environment.update({"GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "never"})
        return subprocess.call(sys.argv[2:], env=environment)  # noqa: S603 - Internal native-command wrapper; structured argv, no shell.
    work = os.environ.get("DRAGON_TASK_WORK_DIR")
    if not work:
        raise ValueError("The Dragon Task work directory is missing.")
    root = bounded_root(Path(work) / "waa-eval")
    report = json.loads((root / "results.json").read_text(encoding="utf-8"))
    try:
        url, branch = source_inputs(os.environ.get("DRAGON_VAR_GIT_URL", ""), os.environ.get("DRAGON_VAR_GIT_BRANCH", ""))
        cache_root = bounded_root(Path.home() / ".fsq-waa" / "git")
        git = GitCommands(root / "git-sync", cache_root / "disabled-hooks")
        source = sync_checkout(url, branch, cache_root, git)
        save_json(root / "git-source.json", source)
        report["git_source"] = source
        write_report(root, report)
        print(f"Git source {source['action']}: {url}, branch {branch}, commit {source['commit']}", flush=True)
    except Exception as error:  # noqa: BLE001 - Script boundary records all failures and returns nonzero.
        report["status"] = "blocked"
        report["errors"].append({"type": type(error).__name__, "message": str(error)})
        (root / "git-source-error.log").write_text(traceback.format_exc(), encoding="utf-8")
        write_report(root, report)
        print(f"Git source synchronization blocked: {error}", file=sys.stderr, flush=True)
        return 1
    else:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
