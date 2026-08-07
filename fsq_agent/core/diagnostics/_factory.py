# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import Protocol

from fsq_agent.core.diagnostics._android import AndroidPlatformProbe
from fsq_agent.core.diagnostics._macos import MacOSPlatformProbe
from fsq_agent.core.diagnostics._web import WebPlatformProbe
from fsq_agent.core.diagnostics._windows import WindowsPlatformProbe
from fsq_agent.models import DiagnosticProbeResult, DoctorProgressSink, HarnessPlatform, HarnessSettings


class PlatformProbe(Protocol):
    def probe(self, timeout_seconds: float = 5.0) -> list[DiagnosticProbeResult]: ...


class PlatformProbeFactory:
    def __init__(self, *, dependencies: dict[str, dict[str, object]] | None = None) -> None:
        self.dependencies = dependencies or {}

    def create(
        self,
        platform: HarnessPlatform,
        harness_settings: HarnessSettings,
        progress_sink: DoctorProgressSink | None = None,
    ) -> PlatformProbe:
        if platform == "android":
            return AndroidPlatformProbe(harness_settings.android, progress_sink=progress_sink, **self.dependencies.get("android", {}))  # type: ignore[arg-type]
        if platform == "web":
            return WebPlatformProbe(harness_settings.web, progress_sink=progress_sink, **self.dependencies.get("web", {}))  # type: ignore[arg-type]
        if platform == "windows":
            return WindowsPlatformProbe(harness_settings.windows, progress_sink=progress_sink, **self.dependencies.get("windows", {}))  # type: ignore[arg-type]
        if platform == "macos":
            return MacOSPlatformProbe(harness_settings.macos, progress_sink=progress_sink, **self.dependencies.get("macos", {}))  # type: ignore[arg-type]
        raise ValueError(f"Unsupported platform: {platform}")
