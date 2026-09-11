# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from fsq_agent.harnesses._android import AndroidHarness
from fsq_agent.harnesses._macos import MacOSHarness
from fsq_agent.harnesses._resources import OwnedResources
from fsq_agent.harnesses._web import WebHarness
from fsq_agent.harnesses._windows import WindowsHarness
from fsq_agent.models import (
    AndroidHarnessSettings,
    ConfigurationError,
    HarnessPlatform,
    HarnessSettings,
    MacOSHarnessSettings,
    RuntimeSecretSettings,
    WebHarnessSettings,
    WindowsHarnessSettings,
)

if TYPE_CHECKING:
    from fsq_agent.core.evidence import ArtifactStore
    from fsq_agent.core.interfaces import (
        AIAssertionEvaluatorProtocol,
        AndroidDriverInterface,
        HarnessInterface,
        MacOSDriverInterface,
        WebDriverInterface,
        WindowsDriverInterface,
    )


class _DriverFactoryProtocol(Protocol):
    def create_android_driver(
        self,
        settings: AndroidHarnessSettings,
        *,
        app_id: str | None = None,
        serial: str | None = None,
    ) -> AndroidDriverInterface: ...

    def create_web_driver(self, settings: WebHarnessSettings) -> WebDriverInterface: ...

    def create_windows_driver(self, settings: WindowsHarnessSettings) -> WindowsDriverInterface: ...

    def create_macos_driver(self, settings: MacOSHarnessSettings) -> MacOSDriverInterface: ...


class _HarnessFactoryProtocol(Protocol):
    def create_harness(
        self,
        *,
        platform: HarnessPlatform,
        harness_settings: HarnessSettings,
        artifact_store: ArtifactStore | None = None,
        ai_assertion_evaluator: AIAssertionEvaluatorProtocol | None = None,
        runtime_secret_settings: RuntimeSecretSettings | None = None,
        app_id: str | None = None,
        serial: str | None = None,
    ) -> HarnessInterface: ...


class _HarnessFactoryImplementation:
    def __init__(self, driver_factory: _DriverFactoryProtocol) -> None:
        self.driver_factory = driver_factory

    def create_harness(
        self,
        *,
        platform: HarnessPlatform,
        harness_settings: HarnessSettings,
        artifact_store: ArtifactStore | None = None,
        ai_assertion_evaluator: AIAssertionEvaluatorProtocol | None = None,
        runtime_secret_settings: RuntimeSecretSettings | None = None,
        app_id: str | None = None,
        serial: str | None = None,
    ) -> HarnessInterface:
        driver = None
        try:
            if platform == "android":
                driver = self.driver_factory.create_android_driver(harness_settings.android, app_id=app_id, serial=serial)
                harness_type = AndroidHarness
            elif platform == "web":
                driver = self.driver_factory.create_web_driver(harness_settings.web)
                harness_type = WebHarness
            elif platform == "windows":
                driver = self.driver_factory.create_windows_driver(harness_settings.windows)
                harness_type = WindowsHarness
            elif platform == "macos":
                driver = self.driver_factory.create_macos_driver(harness_settings.macos)
                harness_type = MacOSHarness
            else:
                raise ConfigurationError("Unsupported harness platform.", context={"platform": platform, "supported": ["android", "web", "windows", "macos"]})  # noqa: TRY301
            return harness_type(
                driver=driver,
                artifact_store=artifact_store,
                ai_assertion_evaluator=ai_assertion_evaluator,
                runtime_secret_settings=runtime_secret_settings,
            )
        except BaseException:
            try:
                OwnedResources(driver, ai_assertion_evaluator).close()
            except BaseException as cleanup_error:  # noqa: BLE001 - construction failure retains precedence.
                logging.getLogger(__name__).warning("Harness construction cleanup failed (%s)", type(cleanup_error).__name__)
            raise
