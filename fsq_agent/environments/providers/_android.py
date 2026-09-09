# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import shutil
import time

from fsq_agent.environments.providers._adb_transport import endpoint, inventory, package_status, valid_package
from fsq_agent.environments.providers._runtime import RuntimeProvider
from fsq_agent.models import AndroidDeviceDiscoveryResult, PlatformPrerequisiteCheck

ANDROID_RUNTIME_PROVIDER = RuntimeProvider(platform="android", module="uiautomator2")
ERRORS = {
    "adb_missing": ("ADB is unavailable.", "Install Android SDK Platform-Tools and make adb available on PATH."),
    "adb_server_unavailable": ("The existing ADB server is unavailable.", "Start ADB manually at the configured local endpoint, then recheck."),
    "adb_timeout": ("ADB diagnosis timed out.", "Check the local ADB server and device connection, then recheck."),
    "adb_endpoint_invalid": (
        "The ADB endpoint configuration is unsupported or invalid.",
        "Use loopback ANDROID_ADB_SERVER_HOST and valid ANDROID_ADB_SERVER_PORT; remove conflicting ADB_SERVER_SOCKET or ADB_SERVER_PORT overrides.",
    ),
    "adb_failed": ("ADB returned an invalid or failed diagnostic response.", "Inspect the existing ADB server and device manually, then recheck."),
}


def discover_android_devices(timeout_seconds: float = 5.0) -> AndroidDeviceDiscoveryResult:
    if shutil.which("adb") is None:
        code = "adb_missing"
    else:
        try:
            return AndroidDeviceDiscoveryResult(devices=inventory(time.monotonic() + min(max(timeout_seconds, 0.01), 10.0)))
        except TimeoutError:
            code = "adb_timeout"
        except OSError:
            code = "adb_server_unavailable"
        except (ValueError, UnicodeError) as exc:
            code = "adb_endpoint_invalid" if str(exc) == "adb_endpoint_invalid" else "adb_failed"
    return AndroidDeviceDiscoveryResult(error_code=code, error_message=ERRORS[code][0])


def fact(identifier, status="ready", message="Requirement is ready.", code=None, action=None, commands=(), target_id=None):
    return PlatformPrerequisiteCheck(identifier=identifier, status=status, message=message, code=code, action=action, commands=commands, target_id=target_id)


def blocked(identifier, dependency):
    return fact(identifier, "not_applicable", f"Check blocked by {dependency}.", "android.dependency_unavailable", "Resolve the preceding requirement and recheck.")


def _selection(devices, serial):
    online = [d for d in devices if d.state == "device"]
    selected = next((d for d in devices if d.serial == serial), None) if serial else online[0] if len(online) == 1 else None
    connection = fact("device_connection", message="An online authorized device is connected.")
    selection = fact("device_selection", message="The exact device selection is available.", target_id=selected.serial if selected else serial or None)
    if serial and selected is None:
        connection = fact(
            "device_connection", "unavailable", "The selected Android device is no longer connected.", "android.selected_device_missing", "Reconnect that device or explicitly select another device."
        )
    elif selected and selected.state != "device":
        state = selected.state if selected.state in {"unauthorized", "offline"} else "unavailable"
        action = "Unlock the device and manually accept USB debugging authorization." if state == "unauthorized" else "Check the USB/network connection and device state manually."
        connection = fact("device_connection", "unavailable", f"The selected device is {state}.", f"android.device_{state}", action)
    elif not online:
        state = devices[0].state if len(devices) == 1 else "unavailable"
        state = state if state in {"unauthorized", "offline"} else "unavailable"
        code = f"android.device_{state}" if devices else "android.no_device"
        connection = fact(
            "device_connection", "unavailable", f"No authorized online Android device is available ({state}).", code, "Connect a device, enable USB debugging and manually authorize this computer."
        )
    if connection.status != "ready":
        selection = blocked("device_selection", "device connection/authorization").model_copy(update={"target_id": serial or None})
    elif selected is None:
        selection = fact(
            "device_selection",
            "unavailable",
            "Multiple online Android devices require explicit selection.",
            "android.selection_required",
            "Select a device in Control Plane, or make the connection unambiguous before running CLI Doctor.",
        )
    return selected, connection, selection


def _installation(serial, app_id):
    status = package_status(serial, app_id)
    if status == "installed":
        return fact("application_installation", message="The configured application is installed on the selected device.")
    if status == "absent":
        return fact(
            "application_installation",
            "unavailable",
            "The application is not installed on the selected device.",
            "android.application_missing",
            "Install the intended application manually or repair its configured package ID.",
        )
    return fact(
        "application_installation",
        "error",
        "The application query timed out." if status == "timeout" else "Application installation could not be checked.",
        "android.application_timeout" if status == "timeout" else "android.application_query_failed",
        "Inspect the selected device and package manager, then recheck; this does not prove the app is missing.",
    )


def android_prerequisites(settings):
    adb = fact("adb_cli", message="ADB is available on PATH.") if shutil.which("adb") else fact("adb_cli", "unavailable", ERRORS["adb_missing"][0], "android.adb_missing", ERRORS["adb_missing"][1])
    try:
        runtime = ANDROID_RUNTIME_PROVIDER.check()
        dependency = (
            fact("uiautomator2_runtime")
            if runtime.ready
            else fact(
                "uiautomator2_runtime", "unavailable", "The uiautomator2 dependency is missing from FSQ.", "android.runtime_missing", "Reinstall or repair FSQ using the same Python environment."
            )
        )
    except Exception:  # noqa: BLE001 -- isolate independent dependency inspection.
        dependency = fact("uiautomator2_runtime", "error", "The Python dependency check failed.", "android.runtime_query_failed", "Inspect the FSQ Python environment.")
    app_id = settings.app_id or ""
    app = (
        fact("application_identifier")
        if valid_package(app_id)
        else fact(
            "application_identifier", "error", "The Android application ID is invalid.", "android.application_identifier_invalid", "Edit the Android target configuration with a valid package ID."
        )
    )
    discovery = discover_android_devices()
    if discovery.error_code:
        code = discovery.error_code
        message, action = ERRORS.get(code, ERRORS["adb_failed"])
        commands = ("adb start-server",) if code == "adb_server_unavailable" and endpoint() == ("127.0.0.1", 5037) else ()
        server = (
            blocked("adb_server", "ADB CLI")
            if code == "adb_missing"
            else fact("adb_server", "error" if code in {"adb_failed", "adb_timeout", "adb_endpoint_invalid"} else "unavailable", message, f"android.{code}", action, commands)
        )
        return (adb, dependency, server, blocked("device_connection", "ADB server"), blocked("device_selection", "device discovery"), app, blocked("application_installation", "device selection"))
    server = fact("adb_server", message="Existing ADB server answered without auto-start.")
    selected, connection, selection = _selection(discovery.devices, settings.serial or "")
    installation = _installation(selected.serial, app_id) if selection.status == "ready" and app.status == "ready" else blocked("application_installation", "device selection or application ID")
    return (adb, dependency, server, connection, selection, app, installation)
