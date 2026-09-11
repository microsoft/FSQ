# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

from fsq_agent.models import ConfigurationError, RuntimeSecretSettings


class RuntimeSecretStore:
    def __init__(self, allowed_names: Iterable[str] = (), values: dict[str, str] | None = None) -> None:
        self._allowed_names = tuple(dict.fromkeys(name.strip() for name in allowed_names if isinstance(name, str) and name.strip()))
        self._allowed = set(self._allowed_names)
        source_values = values or {}
        self._values = {name: str(source_values.get(name) or "") for name in self._allowed_names}
        self._warnings = tuple(f"Runtime secret {name} is configured but not set." for name in self._allowed_names if not self._values.get(name))

    @classmethod
    def from_settings(cls, settings: RuntimeSecretSettings) -> RuntimeSecretStore:
        return cls(settings.allowed_names, settings.private_values())

    @classmethod
    def empty(cls) -> RuntimeSecretStore:
        return cls(())

    def available_names(self) -> tuple[str, ...]:
        return tuple(name for name in self._allowed_names if self._values.get(name))

    def warnings(self) -> tuple[str, ...]:
        return self._warnings

    def redaction_values(self) -> tuple[str, ...]:
        """Supply known private values only to in-process persistence sanitizers."""
        return tuple(value for value in self._values.values() if value)

    def resolve(self, name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise ConfigurationError("Runtime secret name is empty.", context={"name": name})
        if normalized not in self._allowed:
            raise ConfigurationError(
                "Runtime secret name is not allowed.",
                context={"name": normalized, "allowed": list(self._allowed_names)},
            )
        value = self._values.get(normalized) or ""
        if not value:
            raise ConfigurationError("Runtime secret is not set.", context={"name": normalized})
        return value
