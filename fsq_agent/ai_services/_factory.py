# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

from typing import TYPE_CHECKING

from fsq_agent.ai_services._ai_assertion import AIAssertionEvaluator
from fsq_agent.ai_services._case_suggestion import CaseSuggestionAnalyzer
from fsq_agent.providers import build_model_provider_session

if TYPE_CHECKING:
    from fsq_agent.config import Settings


def build_ai_assertion_evaluator(settings: Settings) -> AIAssertionEvaluator:
    return AIAssertionEvaluator(build_model_provider_session(settings))


def build_case_suggestion_analyzer(settings: Settings) -> CaseSuggestionAnalyzer:
    return CaseSuggestionAnalyzer(build_model_provider_session(settings))


def check_case_suggestion_readiness(settings: Settings) -> tuple[bool, str, str]:
    session = None
    try:
        session = build_model_provider_session(settings)
        CaseSuggestionAnalyzer(session)
    except Exception:  # noqa: BLE001 - readiness returns a safe unavailable result.
        return False, "Case suggestion analysis is unavailable.", "Run fsq providers configure for the selected Provider."
    finally:
        if session is not None:
            session.close_sync()
    return True, "Case suggestion analysis is ready.", ""
