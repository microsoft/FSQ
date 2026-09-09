# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Any

from fsq_agent.models import (
    CapabilityRegistrySnapshot,
    ExecutableStep,
    FsqCase,
    SourceRef,
)


class FsqExecutableStepAdapter:
    def __init__(self, registry_snapshot: CapabilityRegistrySnapshot) -> None:
        self.registry_snapshot = registry_snapshot

    def to_executable_steps(self, case: FsqCase) -> list[ExecutableStep]:
        from ._validation import FsqCaseValidator

        FsqCaseValidator(self.registry_snapshot).validate(case)
        return [self._to_step(case, command, index) for index, command in enumerate(case.commands)]

    def _to_step(self, case: FsqCase, command: Any, index: int) -> ExecutableStep:
        from ._validation import FsqCaseValidator

        authored_action_name, capability, params, timeout_ms = FsqCaseValidator(self.registry_snapshot).command(case, command, index)
        action_name = capability.name
        return ExecutableStep(
            step_id=f"{case.id}-step-{index + 1:03d}",
            source_ref=SourceRef(
                source_type="fsq",
                source_id=str(case.path),
                step_index=index,
                metadata={"case_name": case.config.name, "platform": case.config.platform},
            ),
            kind=capability.step_kind,
            action_name=action_name,
            params=params,
            timeout_ms=timeout_ms,
            metadata={
                "case_id": case.id,
                "case_name": case.config.name,
                "platform": case.config.platform,
                "authored_action_name": authored_action_name,
                "capability": capability.safe_metadata() if capability is not None else None,
                "raw_command": command,
            },
        )
