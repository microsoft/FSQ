# FSQ Dev Box Setup

**Scope:** This package owns preparation and readiness acceptance for FSQ on a
Windows Machine that is already connected to Dragon.

**Maintenance:** Update this guide when setup inputs, installed runtime,
authorization, acceptance, reuse, routing, or readiness records change.

## Entry boundary

Install and register Dragon Service using Dragon's supported Machine connection
procedure first. This Pipeline cannot bootstrap a Machine that has no remote
execution connection, and never installs, updates, restarts, or registers
Dragon Service.

Open **Pipelines**, select **FSQ Dev Box Setup**, and select the target remote
Machine. Configure a repository already registered on that Machine as its
scheduling checkout. Select `<target-windows-machine>` explicitly for each Dev Box.
The scheduling checkout is not pulled, reset, cleaned, or used as the installed
FSQ source. Do not select the local Hub as the execution Machine.

## Inputs

| Input | Meaning |
|---|---|
| `git-url` | FSQ source URL, default `https://github.com/microsoft/FSQ.git`. |
| `git-branch` | Fetched branch, default `user/ty/win-eval-dragon`. |
| `bootstrap-python` | Optional remote Python 3.11+ path; omitted uses private Python 3.12.12. |
| `edge-exe` | Optional remote Edge executable path; otherwise standard installations are discovered. |
| `expected-host` | Optional Windows hostname guard, checked before deployment writes. |
| `authorize-provider` | Default true: start official GitHub device authorization only when no Provider exists or saved GitHub authentication is rejected. |
| `setup-model` | Model for new GitHub configuration only; default `gpt-5.6-sol-fast`. Existing working Provider/model stays unchanged. |
| `setup-smoke` | Default false. Optionally execute one original profile-name Case after environment readiness. |

No Hub-local interpreter or Dataset path is required. The complete runtime
package, CSS, and 13 original JSON definitions are transported inside the Stage
with payload and file checksums.

## What Setup verifies

Eight logical checks are visible in Task logs and the setup report:

1. Actual Windows host/account, Git, Edge, and at least 2 GiB free disk space.
2. Complete transported runtime files and fixed Dataset definitions.
3. Configured remote branch resolved to an exact source commit.
4. Private Python/FSQ venv, installed-module origin, inspected native-driver
   fingerprint, and a dependency check, including on an existing environment.
5. Saved Provider plus an actual minimal model connection request.
6. Interactive desktop and real screen-capture availability.
7. Fresh private Edge window, native Ctrl+L/Escape input, UI snapshot, screenshot,
   driver detachment without killing the application, and graceful cleanup.
8. A positive readiness record and the follow-up evaluation entry point.

The checks run inside one self-contained remote script Stage to avoid sibling
asset transport assumptions. They are not eight separate Copilot Stages.
Progress is streamed to the Task while authorization or native acceptance runs.

The Provider probe uses FSQ's official connection-test operation and sends
`Reply with FSQ_OK.` to the saved model. It checks for a usable response, not an
exact-text score, and can incur a small model charge. It does not evaluate a UI
goal, create a Case, or expose response text in the report.

## Authorization and desktop interaction

When GitHub authorization is needed and enabled, the Task displays the official
device-login address and code, and waits for a bounded period. Complete that
authorization yourself. The credentials remain on the remote executing
account. Timeout, denial, or an unsuccessful post-login model request blocks
Setup rather than silently proceeding.

An existing working Provider is preserved. Another configured Provider is
never replaced with GitHub to repair its failure. Rate limits, service errors,
and timeouts do not trigger account switching. New automatic authorization in
this version supports GitHub Copilot; configure another Provider using FSQ's
official interface on the remote account before rerunning Setup.

Keep the target desktop unlocked and the Remote Desktop connection visible.
If capture is unavailable, the report asks you to connect/unlock and start a
fresh Task. No session takeover, lock-screen bypass, or global Edge termination
is performed. The native probe operates only on a fresh owned Profile; do not
use the mouse or keyboard during that short acceptance check.

## Reuse and outcome

The shared installer clones once and fetches the configured branch into an
owned cache. Each resolved SHA has a private source snapshot and venv.
Already prepared environments are reused and rechecked, not globally
reinstalled. Dirty or mismatched source caches block without discarding changes.
An account-local Setup lock covers preparation, authorization, acceptance, and
record publication; the native probe additionally uses the desktop lock.
These locks coordinate this package, not arbitrary manual FSQ commands.

Success publishes an HTML report, JSON preparation record, and evidence ZIP.
The account's `.fsq-waa\setup-ready.json` records that successful observation;
it contains no access tokens. It does not grant permanent readiness or bypass
later evaluation checks. Authorization and desktop state can change afterward.
Failed Setup leaves diagnostic evidence and preserves previous environments.
Very early failures before Python starts use a basic blocked-deployment report.

Default success means **environment ready, zero scoring Cases executed**.
With `setup-smoke=true`, the additional Case result is recorded separately.
A failed smoke Case preserves environment readiness in the report but makes
the Task nonpassing; it is not misrepresented as a successful test or an
installation failure.

After Setup, use **FSQ WAA Remote Eval** on the same Machine. It still checks
current authorization, desktop, source/runtime identity, and test prerequisites.
Other Machines need their own setup and authorization.

## Package maintenance

The implementation is shared with the adjacent
[evaluation package](../fsq-waa-edge-eval/README.md).
The [maintenance build entry](../../README.md) generates this package's
`deploy.ps1` in the output bundle using `build_remote.py` setup mode; the mode is fixed in the generated script,
not inferred from a missing environment variable. Rebuild after runtime or
Dataset pin updates. Do not edit or check in the generated payload by hand.
