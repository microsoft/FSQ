# Module: observation

## Purpose

Persist safe Agent progress and diagnostic timelines under the active workspace's direct Run directory. Core Evidence owns the durable execution journal and checkpoints. Screenshots, UI snapshots, page sources, and other observations are artifact references produced by platform services, capabilities, or AgentTool helpers, not captured here.

## Dependencies

- `models`: Uses `RunEvent`.

## Public Interface

Current `__init__.py` exports via `__all__`:

- `ExecutionLogger`: Writes structured step logs, run-level trace events, and per-run live event timelines.

## Internal Structure

- `__init__.py`: Public exports only.
- `_logger.py`: Structured logging setup and event writing.
- `SPEC.md`: Module design.

## Python Architecture

Observation remains a Level 2 Simple Package: `ExecutionLogger` is exported through `__init__.py`, `_logger.py` owns progress persistence, and Models supplies `RunEvent`. Agent and composition roots consume Observation, which depends only on shared models; it owns neither the execution ledger nor Run lifecycle.

## Error Handling

Event logging failures are treated as I/O errors from the underlying filesystem. Observation capture failures belong to the platform runtime service, PlatformTool, CommonTool, or AgentTool helper that provided the observation capability.

## Current Invariants

- The observation module does not implement screenshot, UI tree, or page-source capture. Current platform observations should be requested through active PlatformTools or harness runtime services; dynamic historical artifact lookup should use AgentTools. If no active capability exposes an observation type, that observation type is unavailable for the run.
- Live run event timelines are persisted as `<workspace>/.fsq/runs/<platform>/<run-id>/events.jsonl`, equivalent to `output.runs_dir/<run-id>/events.jsonl` after workspace-platform settings composition, so interrupted or long-running tasks can be inspected before final reports are generated.
- The timeline accepts the safe `dynamic_agent_token_usage` Run event emitted once for a completed Dynamic Agent main execution and persists it as an ordinary JSONL record; Observation does not calculate, estimate, aggregate, or otherwise interpret token usage.
- `events.jsonl` is separate from `evidence-events.jsonl` and its sequence space. Correlation carries known execution identities without manufacturing missing Core results. Compatibility run-start/completion/failure progress events never finalize metadata or override frozen conclusions.
- Event timelines, reports, and tool artifacts must remain inside the unique current run directory, a direct child of the selected platform run root; observation never discovers or constructs workspace paths.
