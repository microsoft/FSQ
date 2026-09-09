# Android installation and troubleshooting

FSQ checks ADB, the uiautomator2 Python dependency, an existing local ADB server, device connection and authorization, device selection, and the application package. It does not install software, start services, grant permissions, or initialize device automation.

1. Install Android SDK Platform-Tools using your approved installation method and ensure `adb` is on PATH. If the Python dependency is missing, repair the FSQ installation in the same Python environment.
2. Start the default local ADB server manually in your terminal when needed:

```bash
adb start-server
```

3. Connect your device, unlock it, enable Developer options and USB debugging manually, and accept the computer's authorization prompt. An `unauthorized` device needs authorization; an `offline` device needs its connection inspected. FSQ never revokes authorizations or restarts devices automatically.
4. Select the intended online device in Control Plane. When multiple devices are online, CLI Doctor reports selection required; it does not offer a persisted serial setting. With one online authorized device, CLI Doctor can diagnose that device.
5. Install the intended application manually on that device, or correct the App ID through Workspace configuration. A failed or timed-out package query is not proof of a missing app.
6. Select **Recheck environment**. Goal and Case drafts are preserved. Start checks the selected device and effective application again.

## ADB side effects and endpoint scope

Ordinary ADB commands such as `adb devices` can automatically start an ADB server. FSQ diagnosis instead uses bounded protocol reads against an already-running server, with no auto-start fallback even if the server disappears between requests. The server defaults to `127.0.0.1:5037`. Trusted process-level `ANDROID_ADB_SERVER_HOST` and `ANDROID_ADB_SERVER_PORT` can specify an IPv4 loopback endpoint. Explicitly blank values, IPv6, non-loopback, malformed, or conflicting `ADB_SERVER_SOCKET`/`ADB_SERVER_PORT` overrides require manual repair. IPv6 is unsupported because the selected automation backend uses IPv4 sockets. A non-default endpoint needs matching operator startup instructions, not the default command above.

Ready means the listed prerequisites passed. It does not certify device-side uiautomator2 service startup, automation permissions, or a particular Case. Normal execution establishes its own automation session after preflight. Provider is required for Explore and AI-assisted Strict steps, but not ordinary provider-free Strict Replay.
