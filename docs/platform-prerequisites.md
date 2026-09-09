# Platform Prerequisites

`pip install fsq-agent` installs supported Python Driver/Runtime packages. FSQ does not install, start, or modify browsers, applications, devices, ADB, Appium servers, or other host services.

## Web

Provide an installed Chromium-family browser. Supported channels are `chromium`, Chrome stable/beta/dev/canary, and Microsoft Edge stable/beta/dev/canary. `fsq init` discovers an exact channel match when there is one; pass `--browser-executable-path` when discovery is ambiguous or the installation uses a non-standard location. Firefox and WebKit are outside the current Web target contract. FSQ does not install or start the browser during readiness checks.

## Android

ADB must be on `PATH`, an online authorized device must be visible, and the target application must already be installed. Device connection and authorization remain operator responsibilities; FSQ does not connect, authorize, or install devices or applications during readiness checks.

## Windows

Run on Windows with an existing application path and an interactive desktop session that can expose the target UI. The package includes pywinauto; application installation, startup prerequisites, permissions, and desktop-session availability remain operator responsibilities.

## macOS

Run on macOS with full Xcode, Appium CLI, the Appium Mac2 driver, an already reachable Appium service, and an existing application identified by bundle id, application path, or both. Command Line Tools alone are insufficient.

1. Install full Xcode from the Mac App Store, launch it once, and complete Apple's requested setup.
2. Select it as the active developer directory and accept its license when required. If Xcode is installed elsewhere, use that installation's `Contents/Developer` path:

   ```bash
   sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
   sudo xcodebuild -license accept
   ```

3. Install Appium and its Mac2 driver using the operator's approved Node/npm environment:

   ```bash
   npm install -g appium
   appium driver install mac2
   appium driver doctor mac2
   ```

4. Start the service at the configured endpoint (the default is `http://127.0.0.1:4723`):

   ```bash
   appium --address 127.0.0.1 --port 4723
   ```

5. Configure an existing application bundle or executable path, a bundle identifier, or both, then run `fsq doctor` from the registered Workspace root. For Microsoft Edge Stable, the usual values are `/Applications/Microsoft Edge.app` and `com.microsoft.edgemac`. When both are configured, the identifier must match the specified application's `Info.plist`; an executable inside a bundle uses the enclosing bundle's identity.

Doctor still lists independent host checks if the configured application has been moved or uninstalled. Restore it or update its configured path. A damaged `Info.plist` affects the bundle identifier check without discarding the other results. An invalid Workspace document or mismatched Workspace identity must be repaired before target diagnosis.

If macOS prompts for Accessibility or Automation access during an actual test, grant only the permissions appropriate for the operator's environment. Doctor does not trigger those prompts or claim that permission state is ready. It reports full Xcode, the active developer directory, Appium CLI, Mac2 driver, Appium endpoint, application path, and bundle identifier separately. FSQ does not install these dependencies, accept licenses, change system settings, grant permissions, start Appium, or launch the target during diagnosis.

Run `fsq doctor` from the exact registered Workspace root. Doctor does not authenticate interactively, invoke a model, launch a target, create an external session, or repair the host.
