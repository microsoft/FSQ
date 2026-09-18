# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from fsq_agent.application._codex_install import install_codex
from fsq_agent.application.contracts import CodingAgentInstallRequest, CodingAgentInstallResult


def install_coding_agent(request: CodingAgentInstallRequest) -> CodingAgentInstallResult:
    return install_codex(request)


__all__ = ["install_coding_agent"]
