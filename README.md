<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/logo-light.svg">
    <img alt="FSQ — Fully Self Quality" src="docs/assets/logo-light.svg" width="320">
  </picture>
</p>

<h3 align="center">Evidence-first AI UI automation you can inspect, replay, and verify.</h3>

<p align="center">
  <a href="https://github.com/microsoft/FSQ/actions/workflows/ci.yml"><img src="https://github.com/microsoft/FSQ/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 or newer"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-yellow.svg" alt="MIT license"></a>
  <img src="https://img.shields.io/badge/status-alpha-orange" alt="Alpha status">
</p>

<p align="center">
  <a href="#five-minute-quickstart">Quickstart</a> ·
  <a href="README.zh-CN.md">中文</a> ·
  <a href="#coding-agent-workflow">Coding agents</a> ·
  <a href="#how-fsq-works">How it works</a> ·
  <a href="#supported-platforms">Platforms</a> ·
  <a href="docs/getting-started.md">Documentation</a> ·
  <a href="CONTRIBUTING.md">Contributing</a>
</p>

> [!IMPORTANT]
> FSQ v0.1.0 is an alpha release. It is ready for evaluation and contribution, but public APIs and Case authoring details may evolve before 1.0. See [support and stability](docs/support-and-stability.md).

FSQ turns a natural-language UI goal into an observable automation run, saves screenshots, UI snapshots, events, and reports as evidence, and can turn successful actions into a reviewable Case for deterministic replay. It uses Playwright, uiautomator2, pywinauto, and Appium as platform backends; it does not replace or install their host prerequisites.

## Run existing Cases without an LLM

Use FSQ like a test harness once a Case exists. Run history and offline reports do **not** require a configured LLM Provider. Strict replay is also provider-free unless the authored Case contains an AI assertion.

```bash
python -m pip install fsq-agent
fsq init --platform web --browser-channel chrome
# Store reviewed Case assets in your repo, for example: cases/web/*.fsq.yaml
fsq case test --platform web cases/web/YOUR_CASE.fsq.yaml
fsq runs show RUN_ID --open
```

## Create Cases with AI

Configure an LLM Provider, then use `fsq case create --platform web --goal "..."` when you want FSQ to operate the real UI and create a reviewable `.fsq.yaml` Case from the successful Run. Coding agents should provide the goal and context; FSQ proves the path through live execution and evidence.

## See FSQ in action

Watch FSQ turn a natural-language goal into live UI execution, captured evidence, a reviewable Case, and deterministic replay.

https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939

<p align="center">
  <a href="https://youtu.be/QqCahxGDdS0">Watch the full demo on YouTube</a>
</p>

<p align="center">
  <img src="docs/assets/fsq-workflow.svg" alt="FSQ workflow: describe a goal, execute once, capture evidence, verify, review a Case, and replay deterministically" width="880">
</p>

## Why FSQ

- **Inspect the facts.** Every run keeps screenshots, normalized UI snapshots, ordered events, metadata, and reports together.
- **Separate exploration from regression.** AI can explore a goal; reviewed YAML Cases replay authored actions deterministically.
- **Use one workflow across UI surfaces.** Web, Android, Windows, and macOS share the same Case, evidence, Run, and readiness concepts.
- **Keep control local.** Workspaces, evidence, Provider configuration, and the Control Plane are local by default.

FSQ complements platform automation libraries. Playwright, uiautomator2, pywinauto, and Appium perform platform interaction; FSQ adds goal-driven execution, a shared Case format, evidence capture, verification, Run history, and a local Control Plane.

## Product tour

| Describe a goal | Inspect evidence | Review a candidate Case |
|---|---|---|
| <img src="docs/media/01-describe-goal.png" alt="FSQ Control Plane goal entry for a public TodoMVC workflow" width="280"> | <img src="docs/media/03-capture-evidence.png" alt="FSQ evidence view showing persisted UI state from the run" width="280"> | <img src="docs/media/04-generate-candidate.png" alt="FSQ Run-local candidate Case generated from execution facts" width="280"> |

See the remaining approved screenshots in [release media](docs/media/README.md).

## Five-minute quickstart

This public Web example uses [Example Domain](https://example.com/), requires an installed Chromium-family browser, and writes all project data locally. Steps 1-3 exercise the provider-free harness path. A configured Provider is only needed for AI-driven Case creation or post-run suggestions.

### 1. Install

```bash
python -m pip install fsq-agent
```

The base package includes Python dependencies for all four supported platforms. Browsers, applications, devices, ADB, and Appium services remain system prerequisites. FSQ never installs them during `init`.

### 2. Create an empty Workspace

```bash
mkdir fsq-web-demo
cd fsq-web-demo
fsq init --platform web --browser-channel chrome
fsq doctor
```

Workspace root selection is exact:

- In an **empty current directory**, `fsq init` adopts that directory as the Workspace root.
- In a **non-empty current directory**, it creates an absent `<current-directory>/<workspace-name>` child. Use `--name NAME` to choose that name, then change into the child directory for Workspace commands.
- Other CLI commands never search parent directories; run them from the exact registered Workspace root.

### 3. Replay the public example without a planning LLM

Download the versioned [`examples/web/example-domain.fsq.yaml`](examples/web/example-domain.fsq.yaml) into the Workspace and run it:

```bash
mkdir -p cases/web
curl --fail --location --output cases/web/example-domain.fsq.yaml \
  https://raw.githubusercontent.com/microsoft/FSQ/main/examples/web/example-domain.fsq.yaml
fsq case test --platform web cases/web/example-domain.fsq.yaml
fsq runs list --platform web
```

### 4. Explore with AI

Configure one supported user-level Provider from any directory:

```bash
fsq providers configure github_copilot
fsq providers status
```

Then return to the Workspace:

```bash
fsq case create --platform web \
  --goal "Open https://example.com and verify the Example Domain heading is visible."

fsq case test --platform web --suggest cases/web/example-domain.fsq.yaml
fsq runs show RUN_ID --open
```

`--suggest` executes the source Case exactly once, then asks AI to analyze only the persisted Case, report, and evidence. Suggestions and an optional candidate Case remain inside that Run; the source Case is not modified.

### 5. Open the local Control Plane

```bash
fsq ui
```

The installed wheel includes the compiled frontend. It listens on `127.0.0.1:8879` by default and does not require Node.js at runtime.

## Coding agent workflow

Coding agents should not guess UI action steps or hand-author final Case YAML from code context alone. They should understand the product change, provide a precise goal to FSQ, and let FSQ operate the real UI before a Case is reviewed and committed.

```bash
# 1. Ask FSQ to prove a goal against the live UI and record evidence.
fsq case create --platform web \
  --goal "Open https://example.com and verify the Example Domain heading is visible."

# 2. Inspect the generated Run and candidate Case.
fsq runs list --platform web
fsq runs show RUN_ID
fsq runs logs RUN_ID

# 3. Replay the reviewed generated Case deterministically before committing it.
fsq case test --platform web cases/web/RUN_ID.fsq.yaml
```

The durable asset is the reviewed `.fsq.yaml` Case. The proof lives in `.fsq/runs/<platform>/<run-id>/` as events, screenshots, UI snapshots, evidence manifests, and reports. The strict replay path is provider-free, so CI and coding agents can verify committed Cases without configuring another LLM.

## How FSQ works

```text
Goal ──► AI exploration ──► evidence ──► verification ──► reviewable Case
                                                          │
Reviewed Case ──► deterministic replay ──► fresh evidence ─┘
```

Dynamic execution and deterministic replay share platform Harnesses and evidence contracts. The original execution result is immutable; later suggestion analysis cannot rewrite it or perform another UI execution. See the [architecture overview](docs/architecture.md).

## Supported platforms

| Platform | Interaction backend | Host prerequisites |
|---|---|---|
| Web | Playwright | A supported installed Chromium-family channel |
| Android | uiautomator2 | ADB and an online authorized device |
| Windows | pywinauto | Windows and an existing application |
| macOS | Appium Mac2 | macOS, an existing application, and a reachable Appium service |

All Python backend packages are installed with `fsq-agent`; platform applications and host services are not. Run `fsq doctor` from the exact Workspace root for actionable readiness results.

Platform target options for `fsq init`:

| Platform | Required target input |
|---|---|
| Android | `--app-id APP_ID` |
| Web | `--browser-channel CHANNEL`; optional `--browser-executable-path FILE` |
| Windows | `--app-path PATH`; optional `--window-title-re`, `--launch-args` |
| macOS | At least one of `--bundle-id` or `--app-path` |

## Runs and local data

```text
<workspace-root>/
  .fsq/config/config.<platform>.yaml
  .fsq/runs/<platform>/<run-id>/
  cases/<platform>/
  knowledge/<platform>/
```

Use `fsq runs list`, `fsq runs show RUN_ID`, and `fsq runs logs RUN_ID`. `fsq runs show RUN_ID --open` creates an offline static HTML report without calling a Provider or operating the UI. Evidence can contain visible application data; review it before sharing. Do not commit `.fsq`, credentials, reports, screenshots, or private target data.

Provider configuration is stored under `~/.fsq` and shared by the CLI and local Control Plane. Supported first-release Providers are GitHub Copilot and Azure OpenAI.

## Documentation

| Resource | Purpose |
|---|---|
| [中文 README](README.zh-CN.md) | Chinese overview, quickstart, and release links |
| [Getting started](docs/getting-started.md) | Installation, Workspace rules, first Web run, and next commands |
| [中文快速开始](docs/getting-started.zh-CN.md) | Chinese installation and first-run guide |
| [CLI reference](docs/cli-reference.md) | Current public command families and output modes |
| [FSQ Case format](docs/case-format.md) | Case structure and a validated public example |
| [Platform prerequisites](docs/platform-prerequisites.md) | Web, Android, Windows, and macOS host setup boundaries |
| [Support and stability](docs/support-and-stability.md) | Alpha scope, compatibility, privacy, and support expectations |
| [Architecture](docs/architecture.md) | Current runtime layers and ownership boundaries |
| [Release acceptance](docs/release-acceptance-checklist.md) | Maintainer release-candidate verification |
| [Directory migration guide](docs/package-directory-migration-guide.md) | Package ownership and future migration criteria |

## Contributing

Contributions are welcome across documentation, Cases, platform Harnesses, evidence, verification, and developer experience. Start with [CONTRIBUTING.md](CONTRIBUTING.md), follow the [Code of Conduct](CODE_OF_CONDUCT.md), and report vulnerabilities privately through [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE) — Copyright (c) Microsoft Corporation.
