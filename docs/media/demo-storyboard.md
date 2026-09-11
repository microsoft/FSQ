# FSQ README Demo Storyboard

## Deliverable

- Duration: 20 seconds.
- Canvas: 1280×720 source captures; export a 960×540 optimized README GIF.
- Language: silent; concise English overlays make every scene understandable without audio.
- Source: current Control Plane build, a public TodoMVC target, dedicated demo Workspace, and a Provider configuration with no visible secrets.

## Shot list

| Time | Picture | Caption | Proof shown |
|---|---|---|---|
| 00:00–00:04 | Current Test Runner Explore composer | “1 · Goal” | Real natural-language goal and ready Web target |
| 00:04–00:08 | Current Activity and Live evidence split view | “2 · Evidence” | Real persisted TodoMVC screenshot evidence |
| 00:08–00:12 | Current completed Explore run and Save YAML dialog | “3 · Candidate YAML” | Explicit publication boundary for the Run-local recording |
| 00:12–00:16 | Current Strict Replay setup | “4 · Strict Replay” | Validated provider-free deterministic path |
| 00:16–00:20 | Current Runs durable report | “5 · Runs report” | Passed gate, complete evidence, action timeline, and portable HTML export path |

## Recording setup

1. Install the final wheel into a clean environment; do not record from an editable checkout.
2. Set the OS account name, host name, browser profile, bookmarks, notifications, clock, and terminal prompt to neutral demo values.
3. Use a new Workspace named `fsq-demo` under a neutral path. Clear unrelated Workspaces and Runs from the UI.
4. Use a public no-account demo target only. Do not open email, chat, internal portals, private repositories, or account pages.
5. Set browser zoom to 100%, Control Plane zoom to 100%, and terminal font large enough for 1280px export.
6. Disable notifications, password managers, autofill, and clipboard-history overlays.
7. Keep every scene tied to the same public goal, generated Case, evidence chain, and strict replay result.

## Capture checkpoints

- `01-describe-goal.png`: Control Plane goal entry with neutral, real state.
- `02-execute-workflow.png`: active execution timeline without codes, tokens, paths, or device identifiers.
- `03-capture-evidence.png`: real Before/After or UI Tree evidence from the public demo target.
- `04-generate-candidate.png`: Run-local candidate Case generated from persisted execution facts.
- `05-inspect-run.png`: current Runs report with redacted neutral metadata and export entry.

The final repository must not contain placeholder or synthetic product screenshots. If any checkpoint cannot be captured safely, omit it rather than staging false evidence.

## Export

- README GIF: 960×540, 10 fps, optimized palette, target under 8 MB.
- Thumbnail: 1280×720 PNG, derived from a real approved frame.

## Acceptance

Watch the export with audio muted and verify that a first-time user can identify the Goal, evidence, candidate Case, strict replay, durable Runs report, and portable HTML export. Then complete every item in [media acceptance](media-acceptance.md).
