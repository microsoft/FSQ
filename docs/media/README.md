# Release Media

This directory owns the public demo media, product screenshots, subtitles, naming rules, privacy review, and new-user acceptance.

The README first screen uses `fsq-control-plane-demo.gif`, a 20-second silent tour captured from the current Control Plane UI. It shows the real `Goal → Evidence → Candidate YAML → Strict Replay → Runs report → Portable HTML` workflow. The existing v0.1.0 full demo remains available through GitHub media attachment and YouTube.

The current-UI tour is delivered as a lightweight GIF. A separate live-demo video can be recorded and hosted later without committing large video files to Git history. Original local captures are intentionally not committed.

## Naming

- `fsq-v0.1.0-demo-thumbnail.png` — linked video thumbnail;
- `fsq-v0.1.0-android-demo-preview.gif` — lightweight README animation built from approved product screenshots;
- `fsq-control-plane-demo.gif` — current Control Plane README first-screen tour;
- `https://github.com/user-attachments/assets/aa9d0a12-2f93-4894-8349-52a013424939` — GitHub README media preview;
- `01-describe-goal.png` — goal entry and setup;
- `02-execute-workflow.png` — execution timeline;
- `03-capture-evidence.png` — captured evidence;
- `04-generate-candidate.png` — Run-local candidate Case;
- `05-inspect-run.png` — static report inspection;
- `demo.en.srt` and `demo.zh-CN.srt` — subtitles.

Do not commit empty placeholder media files. README references are added only when the corresponding approved artifact exists. Large video files stay outside Git history; the GIF is a short preview, not the canonical full demo.
