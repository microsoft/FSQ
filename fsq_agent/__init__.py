# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fsq_agent.agent import FsqAgent
    from fsq_agent.models import Task, TaskResult

__all__ = ["FsqAgent", "Task", "TaskResult"]


def __getattr__(name: str) -> object:
    if name not in __all__:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(".agent" if name == "FsqAgent" else ".models", __name__)
    return getattr(module, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
