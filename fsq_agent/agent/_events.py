# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

import inspect
import json
import re
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from fsq_agent.models import RunEvent, RunEventSink
from fsq_agent.observation import ExecutionLogger


class RunEventEmitter:
    def __init__(self, logger: ExecutionLogger | None = None, sink: RunEventSink | None = None, *, secret_values: tuple[str, ...] = ()) -> None:
        self.logger = logger
        self.sink = sink
        self.sequence = 0
        self._secret_values = tuple(sorted({value for value in secret_values if value}, key=len, reverse=True))

    async def emit(self, event: RunEvent) -> None:
        self.sequence += 1
        values = _safe_event_value(event.model_dump(mode="json"), self._secret_values)
        values["sequence"] = self.sequence
        sequenced = RunEvent.model_validate(values)
        if self.logger:
            self.logger.write_run_event(sequenced)
        if self.sink:
            result = self.sink(sequenced)
            if inspect.isawaitable(result):
                await result


def _safe_event_value(value, secret_values):
    if isinstance(value, dict):
        secret_keys = {
            "password",
            "passwd",
            "api_key",
            "apikey",
            "authorization",
            "proxy_authorization",
            "cookie",
            "set_cookie",
            "access_token",
            "refresh_token",
            "token",
            "id_token",
            "pwd",
            "private_values",
            "secret",
            "private_value",
            "client_secret",
            "api-key",
        }
        return {
            key: "[REDACTED]"
            if re.sub(r"([a-z0-9])([A-Z])", lambda match: match[1] + "_" + match[2], unquote(str(key))).casefold().replace("-", "_") in secret_keys
            else _safe_event_value(item, secret_values)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_event_value(item, secret_values) for item in value]
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except (ValueError, RecursionError):
            parsed = None
        if isinstance(parsed, (dict, list)):
            return json.dumps(_safe_event_value(parsed, secret_values), ensure_ascii=False)
    for secret in sorted((item for item in secret_values if item), key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")

    def safe_url(match):
        try:
            parts = urlsplit(match.group())
            query = [(key, _safe_event_value({key: item}, secret_values)[key]) for key, item in parse_qsl(parts.query, keep_blank_values=True)]
            return urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, urlencode(query), parts.fragment))
        except ValueError:
            return "[REDACTED_URL]"

    value = re.sub(r"https?://[^\s<>\"']+", safe_url, value, flags=re.I)
    value = re.sub(r"(?im)\b(authorization|proxy[-_]authorization|cookie|set[-_]cookie)\s*[:=]\s*[^\r\n]*", r"\1=[REDACTED]", value)
    value = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[^\s,;]+", "[REDACTED_AUTH]", value)
    value = re.sub(r"(?i)\b((?:access|refresh|id)[_-]?token|token|client[_-]?secret|password|passwd|pwd|api[_-]?key|authorization|cookie|secret)\s*[:=]\s*[^\s,;]+", r"\1=[REDACTED]", value)
    return value
