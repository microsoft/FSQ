# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING, Annotated, BinaryIO, Literal
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator

from fsq_agent.models import ConfigurationError, WorkspaceRegistryEntry

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from fsq_agent.config._settings import Settings

USER_CONFIG_VERSION = 3
USER_CONFIG_FILENAME = "config.yaml"
AUTH_DIRECTORY = "auth"
OPENAI_AUTH_FILENAME = "openai.json"
GEMINI_AUTH_FILENAME = "google-gemini.json"
AZURE_AUTH_FILENAME = "azure-openai.json"
GITHUB_AUTH_FILENAME = "github-copilot-token.json"
GITHUB_PROVIDER_AUTH_FILENAME = "github-copilot-provider-token.json"

_WRITE_LOCK = RLock()
_LOCK_FILENAME = ".config.lock"


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _normalize_azure_base_url(value: str) -> str:
    normalized = _required_text(value, "Azure OpenAI base URL")
    if "/openai/responses" in normalized:
        normalized = normalized.split("/openai/responses", 1)[0] + "/openai/v1/"
    elif "/openai/v1" in normalized:
        normalized = normalized.split("/openai/v1", 1)[0] + "/openai/v1/"
    elif normalized.rstrip("/").endswith((".openai.azure.com", ".cognitiveservices.azure.com")):
        normalized = normalized.rstrip("/") + "/openai/v1/"
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or not normalized.endswith("/openai/v1/"):
        raise ValueError("Azure OpenAI base URL must use the /openai/v1/ form")
    return normalized


class _OpenAIProviderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["openai"]
    model: str

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return _required_text(value, "Model name")


class _GoogleGeminiProviderRecord(_OpenAIProviderRecord):
    type: Literal["google_gemini"]


class _AzureOpenAIProviderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["azure_openai"]
    model: str
    base_url: str

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return _required_text(value, "Model name")

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return _normalize_azure_base_url(value)


class _GitHubCopilotProviderRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["github_copilot"]
    model: str

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        return _required_text(value, "Model name")


_ProviderRecord = Annotated[
    _OpenAIProviderRecord | _AzureOpenAIProviderRecord | _GoogleGeminiProviderRecord | _GitHubCopilotProviderRecord,
    Field(discriminator="type"),
]


class UserProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = USER_CONFIG_VERSION
    provider: _ProviderRecord | None = None
    workspaces: list[WorkspaceRegistryEntry] = Field(default_factory=list)
    _api_key: str = PrivateAttr(default="")
    _github_token: dict[str, object] | None = PrivateAttr(default=None)
    _provider_token: dict[str, object] | None = PrivateAttr(default=None)

    @model_validator(mode="after")
    def _validate_workspace_uniqueness(self) -> UserProviderConfig:
        names: set[str] = set()
        roots: set[str] = set()
        for workspace in self.workspaces:
            normalized_name = workspace.name.casefold()
            normalized_root = os.path.normcase(str(workspace.root_path.resolve()))
            if normalized_name in names:
                raise ValueError("workspace names must be unique")
            if normalized_root in roots:
                raise ValueError("workspace root paths must be unique")
            names.add(normalized_name)
            roots.add(normalized_root)
        return self

    @property
    def api_key(self) -> str:
        return self._api_key

    @property
    def github_token(self) -> dict[str, object] | None:
        return dict(self._github_token) if self._github_token is not None else None

    @property
    def provider_token(self) -> dict[str, object] | None:
        return dict(self._provider_token) if self._provider_token is not None else None


class _WorkspaceRegistryEntryV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    config_path: Path

    @field_validator("config_path")
    @classmethod
    def _validate_config_path(cls, value: Path) -> Path:
        expanded = value.expanduser()
        if not expanded.is_absolute():
            raise ValueError("workspace config_path must be absolute")
        metadata_path = expanded.parent
        workspace_root = metadata_path.parent
        if expanded.is_symlink() or metadata_path.is_symlink() or workspace_root.is_symlink():
            raise ValueError("workspace config_path must not traverse symbolic links")
        normalized = expanded.resolve()
        if normalized.name != "config.yaml" or normalized.parent.name != ".fsq":
            raise ValueError("workspace config_path must identify .fsq/config.yaml")
        return normalized


class _UserProviderConfigV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2]
    provider: _ProviderRecord | None = None
    workspaces: list[_WorkspaceRegistryEntryV2] = Field(default_factory=list)


def load_user_provider_config(user_config_root: str | Path | None = None) -> UserProviderConfig:
    root = _user_config_root(user_config_root)
    with _user_config_lock(root):
        return _load_complete_user_config(root)


def list_workspace_registry(user_config_root: str | Path | None = None) -> list[WorkspaceRegistryEntry]:
    root = _user_config_root(user_config_root)
    with _user_config_lock(root):
        config, _, _ = _load_user_document(root)
        return [entry.model_copy(deep=True) for entry in config.workspaces]


def _register_workspace(entry: WorkspaceRegistryEntry, user_config_root: str | Path | None = None) -> None:
    root = _user_config_root(user_config_root)
    with _user_config_lock(root):
        config, config_path, _ = _load_user_document(root)
        normalized_name = entry.name.casefold()
        normalized_root = os.path.normcase(str(entry.root_path.resolve()))
        if any(existing.name.casefold() == normalized_name for existing in config.workspaces):
            raise ConfigurationError(
                "A workspace with this name is already registered.",
                context={"name": entry.name},
            )
        if any(os.path.normcase(str(existing.root_path.resolve())) == normalized_root for existing in config.workspaces):
            raise ConfigurationError(
                "This workspace path is already registered.",
                context={"root_path": str(entry.root_path)},
            )
        updated = config.model_copy(update={"workspaces": [*config.workspaces, entry]})
        try:
            _atomic_write(config_path, _yaml_bytes(updated))
        except OSError as exc:
            raise ConfigurationError("Unable to register workspace.", context={"name": entry.name}) from exc


def _openai_key(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("API key must be a string")
    normalized = _required_text(value, "OpenAI API key")
    if normalized.lower().startswith("replace-with"):
        raise ValueError("API key contains a placeholder")
    return normalized


def save_openai_provider(
    *,
    model: str,
    api_key: str,
    user_config_root: str | Path | None = None,
) -> UserProviderConfig:
    try:
        provider = _OpenAIProviderRecord(type="openai", model=model)
        normalized_api_key = _openai_key(api_key)
    except (ValidationError, ValueError, TypeError):
        raise ConfigurationError("Invalid OpenAI model or API key.", context={"provider": "openai", "reason": "invalid_candidate"}) from None
    root = _user_config_root(user_config_root)
    with _user_config_lock(root, provider="openai"):
        try:
            current, config_path, auth_dir = _load_user_document(root)
        except ConfigurationError:
            raise ConfigurationError("Unable to read local Provider configuration.", context={"provider": "openai", "reason": "storage"}) from None
        config = current.model_copy(update={"provider": provider})
        config._api_key = normalized_api_key
        _commit_replacement(
            {
                auth_dir / OPENAI_AUTH_FILENAME: _json_bytes({"api_key": normalized_api_key}),
                config_path: _yaml_bytes(config),
            },
            [auth_dir / AZURE_AUTH_FILENAME, auth_dir / GEMINI_AUTH_FILENAME, auth_dir / GITHUB_AUTH_FILENAME, auth_dir / GITHUB_PROVIDER_AUTH_FILENAME],
            provider="openai",
        )
        return config


def _gemini_key(value: str) -> str:
    normalized = _openai_key(value)
    if len(normalized) > 1024 or any(ord(character) < 33 or ord(character) > 126 for character in normalized):
        raise ValueError("Invalid API key")
    return normalized


def save_google_gemini_provider(*, model: str, api_key: str, user_config_root: str | Path | None = None) -> UserProviderConfig:
    try:
        provider = _GoogleGeminiProviderRecord(type="google_gemini", model=model)
        normalized_api_key = _gemini_key(api_key)
    except (ValidationError, ValueError, TypeError):
        raise ConfigurationError("Invalid Google Gemini model or API key.", context={"provider": "google_gemini", "reason": "invalid_candidate"}) from None
    root = _user_config_root(user_config_root)
    with _user_config_lock(root, provider="google_gemini"):
        try:
            current, config_path, auth_dir = _load_user_document(root)
        except ConfigurationError:
            raise ConfigurationError("Unable to read local Provider configuration.", context={"provider": "google_gemini", "reason": "storage"}) from None
        config = current.model_copy(update={"provider": provider})
        config._api_key = normalized_api_key
        _commit_replacement(
            {auth_dir / GEMINI_AUTH_FILENAME: _json_bytes({"api_key": normalized_api_key}), config_path: _yaml_bytes(config)},
            [auth_dir / OPENAI_AUTH_FILENAME, auth_dir / AZURE_AUTH_FILENAME, auth_dir / GITHUB_AUTH_FILENAME, auth_dir / GITHUB_PROVIDER_AUTH_FILENAME],
            provider="google_gemini",
        )
        return config


def save_azure_openai_provider(
    *,
    base_url: str,
    model: str,
    api_key: str,
    user_config_root: str | Path | None = None,
) -> UserProviderConfig:
    try:
        provider = _AzureOpenAIProviderRecord(type="azure_openai", model=model, base_url=base_url)
    except ValidationError as exc:
        raise ConfigurationError("Invalid Azure OpenAI Provider configuration.", context={"error": str(exc)}) from exc
    try:
        normalized_api_key = _required_text(api_key, "Azure OpenAI API key")
    except ValueError as exc:
        raise ConfigurationError(str(exc)) from exc
    if normalized_api_key.lower().startswith("replace-with"):
        raise ConfigurationError("Azure OpenAI API key still contains a placeholder value.")
    root = _user_config_root(user_config_root)
    with _user_config_lock(root):
        current, config_path, auth_dir = _load_user_document(root)
        config = current.model_copy(update={"provider": provider})
        _commit_replacement(
            {
                auth_dir / AZURE_AUTH_FILENAME: _json_bytes({"api_key": normalized_api_key}),
                config_path: _yaml_bytes(config),
            },
            [auth_dir / OPENAI_AUTH_FILENAME, auth_dir / GEMINI_AUTH_FILENAME, auth_dir / GITHUB_AUTH_FILENAME, auth_dir / GITHUB_PROVIDER_AUTH_FILENAME],
        )
        return _load_complete_user_config(root)


def activate_github_copilot_provider(
    *,
    model: str,
    github_token: Mapping[str, object],
    provider_token: Mapping[str, object],
    user_config_root: str | Path | None = None,
) -> UserProviderConfig:
    try:
        provider = _GitHubCopilotProviderRecord(type="github_copilot", model=model)
        github_payload = dict(github_token)
        provider_payload = dict(provider_token)
        _credential_text(github_payload, "access_token", "GitHub OAuth access token")
        _credential_text(provider_payload, "token", "GitHub Copilot provider token")
        _credential_text(provider_payload, "plan", "GitHub Copilot plan")
    except (ValidationError, ValueError) as exc:
        raise ConfigurationError("Invalid GitHub Copilot Provider configuration.", context={"error": str(exc)}) from exc
    root = _user_config_root(user_config_root)
    with _user_config_lock(root):
        current, config_path, auth_dir = _load_user_document(root)
        config = current.model_copy(update={"provider": provider})
        _commit_replacement(
            {
                auth_dir / GITHUB_AUTH_FILENAME: _json_bytes(github_payload),
                auth_dir / GITHUB_PROVIDER_AUTH_FILENAME: _json_bytes(provider_payload),
                config_path: _yaml_bytes(config),
            },
            [auth_dir / AZURE_AUTH_FILENAME, auth_dir / OPENAI_AUTH_FILENAME, auth_dir / GEMINI_AUTH_FILENAME],
        )
        return _load_complete_user_config(root)


def refresh_provider_settings(
    settings: Settings,
    user_config_root: str | Path | None = None,
) -> Settings:
    root = _user_config_root(user_config_root)
    config = load_user_provider_config(root)
    refreshed = settings.model_copy(deep=True)
    provider_settings = refreshed.agent_runtime
    provider_settings.provider = config.provider.type if config.provider is not None else None
    provider_settings.model = config.provider.model if config.provider is not None else ""
    if isinstance(config.provider, _AzureOpenAIProviderRecord):
        provider_settings.base_url = config.provider.base_url
    elif isinstance(config.provider, _GoogleGeminiProviderRecord):
        provider_settings.base_url = "https://generativelanguage.googleapis.com/v1beta/"
    elif isinstance(config.provider, _OpenAIProviderRecord):
        provider_settings.base_url = "https://api.openai.com/v1/"
    else:
        provider_settings.base_url = ""
    provider_settings.api_key = config.api_key
    provider_settings.github_token = config.github_token
    provider_settings.provider_token = config.provider_token
    provider_settings.user_config_root = root
    return refreshed


def _user_config_root(value: str | Path | None) -> Path:
    return Path(value).expanduser() if value is not None else Path.home() / ".fsq"


@contextmanager
def _user_config_lock(root: Path, *, provider: str | None = None) -> Iterator[None]:
    with _WRITE_LOCK:
        try:
            root.mkdir(parents=True, exist_ok=True)
            lock_file = (root / _LOCK_FILENAME).open("a+b")
        except OSError as exc:
            raise ConfigurationError(
                "Unable to lock user Provider configuration.",
                context={"provider": provider, "reason": "storage"} if provider else {"path": str(root)},
            ) from exc
        with lock_file:
            try:
                _acquire_process_lock(lock_file)
            except OSError as exc:
                raise ConfigurationError(
                    "Unable to lock user Provider configuration.",
                    context={"provider": provider, "reason": "storage"} if provider else {"path": str(root)},
                ) from exc
            try:
                yield
            finally:
                _release_process_lock(lock_file)


def _acquire_process_lock(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        lock_file.write(b"\0")
        lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_process_lock(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _ensure_layout(root: Path) -> tuple[Path, Path]:
    config_path = root / USER_CONFIG_FILENAME
    auth_dir = root / AUTH_DIRECTORY
    try:
        root.mkdir(parents=True, exist_ok=True)
        auth_dir.mkdir(parents=True, exist_ok=True)
        if not config_path.exists():
            _atomic_write(config_path, _yaml_bytes(UserProviderConfig()))
    except OSError as exc:
        raise ConfigurationError("Unable to initialize user Provider configuration.", context={"path": str(root)}) from exc
    return config_path, auth_dir


def _load_user_document(root: Path) -> tuple[UserProviderConfig, Path, Path]:
    config_path, auth_dir = _ensure_layout(root)
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigurationError("Unable to read user Provider configuration.", context={"path": str(config_path)}) from exc
    if not isinstance(data, dict):
        raise ConfigurationError("User Provider configuration must contain a YAML mapping.", context={"path": str(config_path)})
    if data.get("version") == 2:
        try:
            legacy = _UserProviderConfigV2.model_validate(data)
        except ValidationError as exc:
            raise ConfigurationError(
                "Invalid user Provider configuration.",
                context={"path": str(config_path), "errors": exc.errors()},
            ) from exc
        try:
            workspaces = [WorkspaceRegistryEntry(name=entry.name, root_path=entry.config_path.parent.parent) for entry in legacy.workspaces]
            upgraded = UserProviderConfig(provider=legacy.provider, workspaces=workspaces)
        except ValidationError as exc:
            raise ConfigurationError(
                "Invalid user Provider configuration.",
                context={"path": str(config_path), "errors": exc.errors()},
            ) from exc
        try:
            _atomic_write(config_path, _yaml_bytes(upgraded))
        except OSError as exc:
            raise ConfigurationError(
                "Unable to upgrade user Provider configuration.",
                context={"path": str(config_path)},
            ) from exc
        return upgraded, config_path, auth_dir
    try:
        config = UserProviderConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(
            "Invalid user Provider configuration.",
            context={"path": str(config_path), "errors": exc.errors()},
        ) from exc
    return config, config_path, auth_dir


def _load_complete_user_config(root: Path) -> UserProviderConfig:
    config, _, auth_dir = _load_user_document(root)
    if config.provider is None:
        return config
    if config.provider.type == "google_gemini":
        try:
            credentials = _read_json_object(auth_dir / GEMINI_AUTH_FILENAME, "Google Gemini credentials")
            config._api_key = _gemini_key(_credential_text(credentials, "api_key", "Google Gemini API key"))
        except (ConfigurationError, ValueError, TypeError):
            raise ConfigurationError("Unable to load valid Google Gemini credentials.", context={"provider": "google_gemini", "reason": "invalid_candidate"}) from None
        return config
    if config.provider.type == "openai":
        try:
            credentials = _read_json_object(auth_dir / OPENAI_AUTH_FILENAME, "OpenAI credentials")
            config._api_key = _openai_key(_credential_text(credentials, "api_key", "OpenAI API key"))
        except (ConfigurationError, ValueError, TypeError):
            raise ConfigurationError("Unable to load valid OpenAI credentials.", context={"provider": "openai", "reason": "invalid_candidate"}) from None
        return config
    if config.provider.type == "azure_openai":
        credentials = _read_json_object(auth_dir / AZURE_AUTH_FILENAME, "Azure OpenAI credentials")
        config._api_key = _credential_text(credentials, "api_key", "Azure OpenAI API key")
        return config
    config._github_token = _read_json_object(auth_dir / GITHUB_AUTH_FILENAME, "GitHub OAuth credentials")
    config._provider_token = _read_json_object(
        auth_dir / GITHUB_PROVIDER_AUTH_FILENAME,
        "GitHub Copilot provider credentials",
    )
    _credential_text(config._github_token, "access_token", "GitHub OAuth access token")
    _credential_text(config._provider_token, "token", "GitHub Copilot provider token")
    _credential_text(config._provider_token, "plan", "GitHub Copilot plan")
    return config


def _read_json_object(path: Path, description: str) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"Unable to read {description}.", context={"path": str(path)}) from exc
    if not isinstance(data, dict):
        raise ConfigurationError(f"{description} must contain a JSON object.", context={"path": str(path)})
    return data


def _credential_text(data: Mapping[str, object], key: str, description: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{description} is required")
    return value


def _yaml_bytes(config: UserProviderConfig) -> bytes:
    return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False).encode("utf-8")


def _json_bytes(data: Mapping[str, object]) -> bytes:
    return json.dumps(dict(data), separators=(",", ":")).encode("utf-8")


def _commit_replacement(payloads: Mapping[Path, bytes], removals: list[Path], *, provider: str | None = None) -> None:
    affected = list(dict.fromkeys([*payloads, *removals]))
    snapshots: dict[Path, bytes | None] = {}
    staged: dict[Path, Path] = {}
    try:
        for path in affected:
            snapshots[path] = path.read_bytes() if path.exists() else None
        for path, payload in payloads.items():
            staged[path] = _stage_write(path, payload)
        for path, temporary_path in staged.items():
            temporary_path.replace(path)
        for path in removals:
            path.unlink(missing_ok=True)
    except OSError as exc:
        restored = _restore_snapshots(snapshots)
        context = {"provider": provider, "reason": "storage" if restored else "internal"} if provider else None
        raise ConfigurationError("Unable to persist user Provider configuration.", context=context) from exc
    finally:
        for temporary_path in staged.values():
            temporary_path.unlink(missing_ok=True)


def _restore_snapshots(snapshots: Mapping[Path, bytes | None]) -> bool:
    restored = True
    for path, payload in snapshots.items():
        try:
            if payload is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, payload)
        except OSError:
            restored = False
    return restored


def _atomic_write(path: Path, payload: bytes) -> None:
    temporary_path = _stage_write(path, payload)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _stage_write(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path
