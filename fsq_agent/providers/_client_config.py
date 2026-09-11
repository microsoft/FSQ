# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ProviderClientConfig:
    provider: str
    model: str
    api_key: str = field(repr=False)
    base_url: str
    default_headers: dict[str, str] = field(default_factory=dict, repr=False)
    metadata: dict[str, Any] = field(default_factory=dict)
    backend: Literal["openai_responses", "google_interactions"] = "openai_responses"
    model_name_is_deployment: bool = False
