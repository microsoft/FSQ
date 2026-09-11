# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol, runtime_checkable

from fsq_agent.models import (
    AIAssertionRequest,
    AIAssertionResult,
    EvidenceArtifactRef,
    ExecutableStep,
    FailureCategory,
    HarnessActionResult,
    HarnessArtifactRef,
    HarnessContext,
    HarnessFunctionSchema,
    StepPhase,
)


@runtime_checkable
class AIAssertionEvaluatorProtocol(Protocol):
    def close(self) -> None: ...

    def evaluate(self, request: AIAssertionRequest) -> AIAssertionResult: ...


@runtime_checkable
class DriverObservationInterface(Protocol):
    def close(self) -> None: ...

    def screenshot(self, params: object | None = None) -> bytes: ...

    def ui_snapshot(self, params: object | None = None) -> dict[str, object]: ...


@runtime_checkable
class HarnessInterface(Protocol):
    def close(self) -> None: ...

    def capture_scope(self, callback: Callable[[EvidenceArtifactRef], object]) -> AbstractContextManager: ...

    def get_context(self) -> HarnessContext: ...

    def action_space(self) -> list[HarnessFunctionSchema]: ...

    def before_action(self, step: ExecutableStep, context: HarnessContext) -> None: ...

    def invoke_action(self, step: ExecutableStep, context: HarnessContext) -> HarnessActionResult: ...

    def after_action(
        self,
        step: ExecutableStep,
        context: HarnessContext,
        action_result: HarnessActionResult,
    ) -> None: ...

    def capture_artifact(
        self,
        kind: str,
        reason: str,
        context: HarnessContext,
        step_id: str,
        phase: StepPhase,
    ) -> HarnessArtifactRef: ...

    def classify_error(self, error: BaseException, phase: StepPhase, step: ExecutableStep) -> FailureCategory: ...
