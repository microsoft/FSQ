# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
from pydantic import BaseModel, ConfigDict, model_serializer, model_validator


class ProviderConfigurationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str = "success"
    provider: str
    model: str
    region: str | None = None
    configured: bool = True

    @model_validator(mode="after")
    def _validate_region(self):
        if self.provider == "kimi":
            if self.region not in {"cn", "global"}:
                raise ValueError("Kimi Provider results require region cn or global")
        elif self.region is not None:
            raise ValueError("Only Kimi Provider results may include a region")
        return self

    @model_serializer
    def _serialize(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "provider": self.provider,
            "model": self.model,
            "configured": self.configured,
        }
        if self.region is not None:
            result["region"] = self.region
        return result


class ProviderStatusResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    status: str
    configured: bool
    provider: str | None = None
    model: str | None = None
    region: str | None = None
    authenticated: bool
    message: str
    action: str | None = None

    @model_validator(mode="after")
    def _validate_region(self):
        if self.provider == "kimi":
            if self.region not in {"cn", "global"}:
                raise ValueError("Kimi Provider status requires region cn or global")
        elif self.region is not None:
            raise ValueError("Only Kimi Provider status may include a region")
        return self

    @model_serializer
    def _serialize(self) -> dict[str, object]:
        result: dict[str, object] = {
            "status": self.status,
            "configured": self.configured,
            "provider": self.provider,
            "model": self.model,
            "authenticated": self.authenticated,
            "message": self.message,
            "action": self.action,
        }
        if self.region is not None:
            result["region"] = self.region
        return result


__all__ = ["ProviderConfigurationResult", "ProviderStatusResult"]
