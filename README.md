# fsq-agent

fsq-agent is a goal-driven automated testing agent for FSQ YAML-guided tasks. It executes harness-generated platform actions plus common local utilities, captures evidence, verifies one pre-plan-derived goal, and generates reports.

The project follows spec-driven development. See root [SPEC.md](SPEC.md) and each relevant module `SPEC.md` before changing public interfaces.

## Environment Setup

This project uses `uv` for dependency management. Install `uv` once, then sync the locked project environment from `pyproject.toml` and `uv.lock`.

On Windows PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv sync --extra dev
```

On macOS/Linux shells:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync --extra dev
```

Run CLI commands through `uv run` so they use the synced virtual environment. When project dependencies change, refresh and commit the lock file with `uv lock`.

## Platform Setup

Platform defaults are maintained by the repository for now. For normal local use, copy the example environment file, edit `.env`, choose the target platform in the CLI command, initialize the current directory workspace, and run.

On Windows PowerShell:

```powershell
copy .env.example .env
```

On macOS/Linux shells:

```bash
cp .env.example .env
```

### Android

Install the Android extra and connect an emulator or device with ADB:

```powershell
uv sync --extra dev --extra android
```

Set Android values in `.env`:

```dotenv
FSQ_ANDROID_APP_ID=com.microsoft.emmx
FSQ_ANDROID_SERIAL=emulator-5554
```

Leave `FSQ_ANDROID_SERIAL` blank when only one Android target is connected.

Start Android runs:

```powershell
uv run fsq-agent init --platform android --provider github_copilot
uv run fsq-agent run --platform android --goal "Access Downloads through the browser overflow menu from the New Tab Page, then return to the New Tab Page."
```

### Web With Local Chrome

Install the Web extra and point fsq-agent at the local browser executable:

```powershell
uv sync --extra dev --extra web
```

Set Web values in `.env`:

```dotenv
FSQ_WEB_BROWSER_EXECUTABLE_PATH=C:\Program Files\Google\Chrome\Application\chrome.exe
```

Start Web runs:

```powershell
uv run fsq-agent init --platform web --provider github_copilot
uv run fsq-agent run --platform web --goal "Open https://www.bing.com, search for Playwright, and verify the results page is visible."
```

### Windows Desktop With Edge

Install the Windows extra and point fsq-agent at the target application:

```powershell
uv sync --extra dev --extra windows
```

Set Windows values in `.env`:

```dotenv
FSQ_WINDOWS_APP_PATH=C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe
FSQ_WINDOWS_BACKEND_KIND=uia
FSQ_WINDOWS_WINDOW_TITLE_RE=.*Microsoft.*Edge.*
FSQ_WINDOWS_LAUNCH_ARGS=--no-first-run --disable-features=msImplicitSignin
```

`FSQ_WINDOWS_BACKEND_KIND` is the pywinauto automation mode for the target app. Use `uia` first; switch to `win32` only when the app exposes better controls through the older Win32 backend.

Start Windows desktop runs:

```powershell
uv run fsq-agent init --platform windows --provider github_copilot
uv run fsq-agent run --platform windows --goal "Launch Edge, search for Windows automation, and verify the results page is visible."
```

### macOS With Appium Mac2

Install the macOS extra. Appium 2 and the Mac2 driver must be installed and running on the Mac being automated:

```bash
uv sync --extra dev --extra macos
npm install -g appium
appium driver install mac2
appium --address 127.0.0.1 --port 4723
```

Set macOS values in `.env`:

```dotenv
FSQ_MACOS_APPIUM_SERVER_URL=http://127.0.0.1:4723
FSQ_MACOS_BUNDLE_ID=com.microsoft.edgemac
FSQ_MACOS_APP_PATH=/Applications/Microsoft Edge.app
```

Start macOS runs:

```bash
uv run fsq-agent init --platform macos --provider github_copilot
uv run fsq-agent run --platform macos --goal "Open Microsoft Edge, inspect the visible window, and verify the expected controls are visible."
```

Existing process environment variables take precedence over `.env` values. Secret values such as API keys and test-account passwords should stay in `.env` or the process environment.

Initialize the current directory workspace with the platform and, when running dynamic LLM tasks or strict cases that use `assertWithAI`, the model provider:

```powershell
uv run fsq-agent init --platform <platform> --provider github_copilot
```

`--provider` is optional. When omitted, `init` checks platform readiness without changing provider settings or starting provider authentication. `--provider github_copilot` writes `FSQ_LLM_PROVIDER=github_copilot` to `.env` and may start GitHub device-code authorization; the token cache is stored under `.fsq-agent-workspace/auth/github-copilot-token.json`. Use Azure OpenAI instead with:

```powershell
uv run fsq-agent init --platform <platform> --provider azure_openai
```

Azure setup prompts for `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_MODEL`, and `AZURE_OPENAI_API_KEY`; API key input is hidden.

## CLI Examples

Use the platform that matches the target: `android`, `web`, `windows`, or `macos`.

Initialize and check readiness:

```powershell
uv run fsq-agent init --platform <platform>
uv run fsq-agent init --platform <platform> --provider github_copilot
```

Diagnose local readiness and receive actionable fixes:

```powershell
uv run fsq-agent doctor
uv run fsq-agent doctor --platform android
uv run fsq-agent doctor --platform web --non-interactive
uv run fsq-agent doctor --platform windows --format json --non-interactive
uv run fsq-agent doctor --platform android --color always
```

Every valid `doctor` run performs the complete environment, workspace, provider, and selected-platform diagnosis and returns one overall result. In an interactive terminal, an eligible problem appears as `ACTION REQUIRED`; Doctor immediately asks whether to apply the narrowly scoped safe repair, verifies an accepted repair before continuing, and shows `PASS` only after verification succeeds. Declined, skipped, failed, or unresolved repairs keep their final `WARN` or `FAIL` severity. In CI, redirected execution, or `--non-interactive`, Doctor never prompts or displays `ACTION REQUIRED`. `--repair` immediately applies and verifies eligible no-input repairs such as initializing a missing workspace, while input-required repairs are skipped. Non-secret platform values are written only when entered interactively. Existing `.env` files are backed up before Doctor changes them.

Text-mode Doctor gives every concrete check its own bracketed section derived from the stable check id. Detection, action, repair, verification, result, and manual guidance stay under the same title, for example `[Appium status]`; duplicate phase headings are omitted. Interactive terminals replace transient `RUNNING` and action content in place, while redirected output keeps the same section hierarchy as append-only text. The final two lines show `Summary: PASS|FAIL|ERROR|CANCELLED` and aggregate check counts. Use `--color auto|always|never` to control status highlighting. JSON remains a single progress-free, color-free document and does not contain display titles.

Doctor performs bounded online/local connectivity checks by default but never sends a model inference request, starts provider device-code login, launches the target application, or creates an Appium session. It checks ADB installation/device/package readiness for Android, isolated Chrome startup for Web, static pywinauto target readiness for Windows, and Appium `/status` plus target configuration for macOS. Dependency installation, ADB/Appium service control, provider login, and secret entry remain explicit user actions shown as remediation commands.

Exit codes are `0` when no final check failed (warnings allowed), `1` when any final check failed, `2` for invalid or unresolved command usage, and `130` for user interruption. JSON output is diagnosis-only and cannot be combined with `--repair`.

Run from a natural-language goal:

```powershell
uv run fsq-agent run --platform <platform> --goal "Open the target app and verify the expected page or controls are visible."
```

Run from FSQ case files as dynamic reference material:

```powershell
uv run fsq-agent run --platform <platform> --case-yaml path/to/case.codex.yaml
uv run fsq-agent run --platform <platform> --case-dir path/to/cases
```

Run authored FSQ cases through deterministic strict-core execution:

```powershell
uv run fsq-agent run --platform <platform> --strict --case-yaml path/to/case.codex.yaml
uv run fsq-agent run --platform <platform> --strict --case-dir path/to/cases
```

Open the local playground or print a stored report:

```powershell
uv run fsq-agent playground --platform <platform>
uv run fsq-agent report --platform <platform> --run-id RUN_ID --format markdown
```

## Playground Frontend

The Playground browser source is an npm/Vite project and requires Node.js 20.19+ or 22.12+. Build its production assets before starting the Playground from a source checkout:

```powershell
npm ci
npm run build
uv run fsq-agent playground --platform <platform>
```

The Python server prints its local URL and serves both the generated frontend and Playground APIs. Prebuilt Python wheels already contain the generated assets, so wheel users do not need Node.js.

For frontend development, run the Python API and Vite development server in separate terminals:

```powershell
# Terminal 1
uv run fsq-agent playground --platform <platform> --host 127.0.0.1 --port 8878

# Terminal 2
npm run dev
```

Open the Vite Playground URL, normally `http://127.0.0.1:5173/playground/`. Vite proxies Playground API and streaming requests to `http://127.0.0.1:8878`. Set `FSQ_PLAYGROUND_API_ORIGIN` before `npm run dev` to use another Python origin.

## Current Scope

This implementation provides validated models, configuration loading, runtime wiring, harness/driver configuration, common local tooling, descriptive skill loading, evidence manifests, and report generation. Task execution requires authentication for the selected model provider.

Runtime artifacts are written under the fsq-agent workspace `output` directory. Shell execution settings are no longer part of runtime configuration.
