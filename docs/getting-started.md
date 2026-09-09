# Getting Started

This guide takes a new user from installation to a deterministic Web run against public [TodoMVC](https://todomvc.com/examples/react/dist/). FSQ v0.1.0 is alpha software; review [support and stability](support-and-stability.md) before production adoption.

## Prerequisites

- Python 3.11 or newer.
- A supported installed Chromium-family browser. The examples use stable Chrome.
- An empty directory you can use as a local FSQ Workspace.

AI exploration and suggestion analysis also require OpenAI, Google Gemini, GitHub Copilot, or Azure OpenAI. Deterministic Case replay does not require a planning LLM unless the authored Case contains an AI assertion.

## Install

```bash
python -m pip install fsq-agent
fsq --help
```

The package includes all supported platform Python dependencies. FSQ does not install browsers, applications, ADB, devices, Appium services, or other host prerequisites.

## Initialize an empty Workspace

```bash
mkdir fsq-web-demo
cd fsq-web-demo
fsq init --platform web --browser-channel chrome
fsq doctor
```

An empty current directory becomes the Workspace root. When the current directory is non-empty, `init` preserves it and creates an absent `<current-directory>/<workspace-name>` child instead. Other Workspace commands must run from the exact registered root; they do not search parent directories.

## Run the public deterministic example

Download the current [`examples/web/example-domain.fsq.yaml`](../examples/web/example-domain.fsq.yaml) into `cases/web/` in the Workspace, then run:

```bash
mkdir -p cases/web
curl --fail --location --output cases/web/example-domain.fsq.yaml \
  https://raw.githubusercontent.com/microsoft/FSQ/main/examples/web/example-domain.fsq.yaml
fsq case test --platform web cases/web/example-domain.fsq.yaml
fsq runs list --platform web
```

The Case starts the configured browser, opens TodoMVC, adds two tasks, completes the first task, filters to active tasks, verifies the expected visible state, and closes the browser. Evidence is stored below `.fsq/runs/web/<run-id>/`.

## Configure AI exploration

```bash
fsq providers configure github_copilot
fsq providers status
```

Alternatively run `fsq providers configure openai` for the official OpenAI API, or `fsq providers configure azure_openai` for an Azure deployment. Provider configuration is user-level, stored below `~/.fsq`, and shared with the local Control Plane. OpenAI prompts privately for an API key and offers eligible models; even a single model requires explicit selection.

For browser configuration, run `fsq ui` and open **Settings**:

1. Choose **Add configuration** or **Change provider**, then **OpenAI** or **Google Gemini**.
2. Enter an API key and select **Load models**. Select one offered model, then **Save changes**.
3. Select **Test connection** to send a minimal request using the saved configuration.
4. Return to **Home** to see the Provider summary, then open **Test Runner**, choose a Workspace/platform/target, and start an Explore task explicitly.

OpenAI always uses `https://api.openai.com/v1/` and the Responses API. Its picker offers general-purpose GPT major version 5 or later, excluding mini, nano, Codex, embedding, audio, realtime, image, search, transcription, and TTS variants. An empty list cannot be saved. Changing the key invalidates the list and selection; reload before saving. Azure instead requires a resource endpoint, **deployment name** (not necessarily an OpenAI model id), and API key.

For Gemini, obtain an API key from [Google AI Studio](https://aistudio.google.com/apikey). Use the browser flow above or run `fsq providers configure google_gemini`; the interactive command hides the key and requires an explicit model selection. The picker loads all bounded model-list pages and offers only stable Gemini 3-or-later general-purpose Flash/Pro ids with content-generation support. Preview, Latest, Experimental, Lite, image/audio/embedding and other specialized variants are excluded. An incomplete discovery is an error, not a partial selectable list. The fixed Developer API uses native Interactions with `store=false` and local conversation history; Vertex AI, ADC/service accounts, and custom endpoints are not supported.

Gemini metadata lives in the version-3 user configuration and its plaintext key in `~/.fsq/auth/google-gemini.json`. The same saved Provider serves pre-planning, execution, final verification, AI visual assertions, suggestions, and connection tests. **Test connection** proves a minimal saved-model request, not every tool/schema feature or Case. Start Explore explicitly, inspect its evidence, and save the generated Case using the normal flow; Strict Replay uses Gemini only when its Case requires AI assertions.

Saving rechecks model visibility but does not run inference. Readiness/status is a local configuration check, not a connection test. API keys are plaintext local credentials, masked in Settings by default but returned in full by the trusted-loopback Config API. Successful Provider replacement removes inactive credentials. Neither configuration nor execution falls back to environment variables or another Provider.

If the save response is lost, the page reports an unknown outcome and re-reads configuration. Use **Reload configuration** to retry recovery. Closing the page does not cancel a server save; the current snapshot is not proof that an earlier request finished. FSQ does not automatically resubmit or undo the save. After recovery, review the current Provider and any remaining unsaved draft before continuing.

## Explore and inspect

```bash
fsq case create --platform web --goal "Open https://example.com and verify the Example Domain heading is visible."
fsq runs list
fsq runs show RUN_ID
fsq runs logs RUN_ID
fsq runs show RUN_ID --open
```

The final command creates an offline report from persisted Run facts. It does not operate the target UI or invoke a Provider.

## Analyze a deterministic run

```bash
fsq case test --platform web --suggest cases/web/example-domain.fsq.yaml
```

The Case is executed exactly once. AI analysis then consumes only the source Case, report, and persisted evidence. Suggestions and any candidate Case remain inside the corresponding Run.

## Open the Control Plane

```bash
fsq ui
```

The installed frontend is served locally on `127.0.0.1:8879` by default.

## Next steps

- Review [platform prerequisites](platform-prerequisites.md).
- Learn the [Case format](case-format.md).
- See the [CLI reference](cli-reference.md).
- Review the root and module `SPEC.md` files for implementation-level architecture and behavior contracts.
