# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import Protocol, TypeVar


_T = TypeVar("_T")


class DoctorPrompter(Protocol):
    def choose(self, message: str, options: list[_T], default: _T) -> _T: ...
    def confirm(self, message: str, default: bool = True) -> bool: ...
    def text(self, message: str) -> str: ...


class ConsoleDoctorPrompter:
    def choose(self, message: str, options: list[_T], default: _T) -> _T:
        labels = ", ".join(str(option) for option in options)
        value = input(f"{message} [{labels}] ({default}): ").strip()
        if not value:
            return default
        for option in options:
            if str(option) == value:
                return option
        return default

    def confirm(self, message: str, default: bool = True) -> bool:
        suffix = "Y/n" if default else "y/N"
        value = input(f"{message} [{suffix}]: ").strip().lower()
        if not value:
            return default
        return value in {"y", "yes"}

    def text(self, message: str) -> str:
        return input(f"{message}: ").strip()
