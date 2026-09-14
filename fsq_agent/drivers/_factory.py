# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import cast

from fsq_agent.drivers.android._uiautomator2 import UiAutomator2AndroidDriver
from fsq_agent.drivers.macos._appium_mac2 import AppiumMac2Driver
from fsq_agent.drivers.web._playwright import PlaywrightWebDriver
from fsq_agent.drivers.windows._pywinauto import PywinautoWindowsDriver
from fsq_agent.models import ConfigurationError, HarnessPlatform

_BACKENDS = {"android": "uiautomator2", "web": "playwright", "windows": "pywinauto", "macos": "appium_mac2"}
_DRIVER_CLASSES: dict[HarnessPlatform, type[object]] = {
    "android": cast("type[object]", UiAutomator2AndroidDriver),
    "web": cast("type[object]", PlaywrightWebDriver),
    "windows": cast("type[object]", PywinautoWindowsDriver),
    "macos": cast("type[object]", AppiumMac2Driver),
}


def _driver_class_for_backend(platform: HarnessPlatform, backend: str | None = None) -> type[object]:
    selected = backend or _BACKENDS[platform]
    _ensure_backend(platform, selected)
    return _DRIVER_CLASSES[platform]


def _ensure_backend(platform: HarnessPlatform, backend: str) -> None:
    if backend != _BACKENDS[platform]:
        raise ConfigurationError(f"Unsupported {platform} harness backend.", context={"platform": platform, "backend": backend, "supported": [_BACKENDS[platform]]})


class _DriverFactoryImplementation:
    def create_android_driver(self, settings, *, app_id=None, serial=None):
        _ensure_backend("android", settings.backend)
        return UiAutomator2AndroidDriver(app_id=app_id if app_id is not None else settings.app_id or "", serial=serial if serial is not None else settings.serial)

    def create_web_driver(self, settings):
        _ensure_backend("web", settings.backend)
        viewport = None if settings.viewport_width is None or settings.viewport_height is None else (settings.viewport_width, settings.viewport_height)
        return PlaywrightWebDriver(
            channel=settings.channel,
            executable_path=settings.browser_executable_path,
            headless=settings.headless,
            base_url=settings.base_url,
            viewport=viewport,
            attach=settings.attach,
            attach_endpoint=settings.attach_endpoint,
        )

    def create_windows_driver(self, settings):
        _ensure_backend("windows", settings.backend)
        return PywinautoWindowsDriver(app_path=settings.app_path, backend_kind=settings.backend_kind, window_title_re=settings.window_title_re, launch_args=settings.launch_args)

    def create_macos_driver(self, settings):
        _ensure_backend("macos", settings.backend)
        return AppiumMac2Driver(
            server_url=settings.appium_server_url or "",
            bundle_id=settings.bundle_id,
            app_path=settings.app_path,
            page_source_max_depth=settings.page_source_max_depth,
            action_timeout_seconds=settings.action_timeout_seconds,
            new_command_timeout_seconds=settings.new_command_timeout_seconds,
        )
