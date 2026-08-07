# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from fsq_agent.config import validate_doctor_environment_value
from fsq_agent.models import DoctorRepairAction


REPAIR_AFFECTED_CHECKS: dict[DoctorRepairAction, tuple[str, ...]] = {
    "workspace.initialize": ("workspace.initialized",),
    "environment.update": ("config.valid",),
    "provider.refresh_copilot_token": (
        "provider.github_copilot.credentials",
        "provider.github_copilot.endpoint",
    ),
}


validate_platform_environment_value = validate_doctor_environment_value
