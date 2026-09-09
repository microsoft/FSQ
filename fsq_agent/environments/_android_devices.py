# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.environments.providers._android import discover_android_devices
from fsq_agent.models import AndroidDeviceDiscoveryResult


class AndroidDeviceDiscovery:
    def discover(self, *, timeout_seconds: float = 5.0) -> AndroidDeviceDiscoveryResult:
        return discover_android_devices(timeout_seconds)
