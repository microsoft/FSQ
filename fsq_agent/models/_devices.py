# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AndroidDevice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    serial: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.:@-]+$")
    state: str = Field(min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class AndroidDeviceDiscoveryResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    devices: list[AndroidDevice] = Field(default_factory=list)
    error_code: Literal["adb_missing", "adb_timeout", "adb_start_failed", "adb_failed", "adb_server_unavailable", "adb_endpoint_invalid"] | None = None
    error_message: str | None = None

    @model_validator(mode="after")
    def _validate_error(self) -> "AndroidDeviceDiscoveryResult":
        if (self.error_code is None) != (self.error_message is None):
            raise ValueError("error_code and error_message must be supplied together")
        if self.error_message is not None and not self.error_message.strip():
            raise ValueError("error_message must not be empty")
        return self
