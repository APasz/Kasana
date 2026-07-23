"""Filesystem-backed non-secret configuration for Kasana applications."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from secrets import token_urlsafe
from typing import Any, cast

from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

_CONFIGURATION_DIRECTORY_ENVIRONMENT_VARIABLE = "KASANA_CONFIG_DIRECTORY"
_APPLICATION_CONFIGURATION_PREFIX = "config."
_APPLICATION_CONFIGURATION_SUFFIX = ".json"
DEFAULT_KATALOG_API_HOST = "127.0.0.1"
DEFAULT_KATALOG_API_PORT = 5373


def configuration_directory() -> Path:
    """Return the explicit configuration root without mixing it into application settings."""

    configured_directory = os.environ.get(_CONFIGURATION_DIRECTORY_ENVIRONMENT_VARIABLE)
    return Path(configured_directory) if configured_directory else Path("configs")


def application_configuration_path(section: str) -> Path:
    """Return the non-secret preferences document for one application domain."""

    if not section.isidentifier():
        raise ValueError(f"Invalid application configuration section {section!r}.")
    return configuration_directory() / (
        f"{_APPLICATION_CONFIGURATION_PREFIX}{section}{_APPLICATION_CONFIGURATION_SUFFIX}"
    )


def user_configuration_directory() -> Path:
    """Return the directory whose numeric children represent user IDs."""

    return configuration_directory() / "users"


def kanvas_session_secret() -> str:
    """Load Kanvas's persistent session-signing secret, creating it privately once.

    An environment setting remains available for managed deployments.  The local
    fallback is deliberately outside the JSON preference documents: those are
    non-secret and may be copied or inspected during normal administration.
    """

    path = configuration_directory() / "kanvas.session-secret"
    try:
        secret = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return _create_kanvas_session_secret(path)
    _require_owner_only_file(path)
    if len(secret) < 32:
        raise ValueError(f"Kanvas session secret at {path} must be at least 32 characters.")
    return secret


def _create_kanvas_session_secret(path: Path) -> str:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    secret = token_urlsafe(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return kanvas_session_secret()
    with os.fdopen(descriptor, "w", encoding="utf-8") as secret_file:
        secret_file.write(f"{secret}\n")
    return secret


def _require_owner_only_file(path: Path) -> None:
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise ValueError(f"Kanvas session secret at {path} must have mode 0600, not {mode:04o}.")


def configured_katalog_api_url() -> str:
    """Derive Katalog's endpoint from its configuration domain and env overrides."""

    configuration = _load_document(application_configuration_path("katalog"))
    host = os.environ.get(
        "KASANA_KATALOG_API_HOST", configuration.get("api_host", DEFAULT_KATALOG_API_HOST)
    )
    port = os.environ.get(
        "KASANA_KATALOG_API_PORT", configuration.get("api_port", DEFAULT_KATALOG_API_PORT)
    )
    return katalog_api_url(_configuration_host(host), _configuration_port(port))


def katalog_api_url(host: str, port: int) -> str:
    """Build the canonical Katalog HTTP endpoint from validated bind values."""

    rendered_host = f"[{host}]" if ":" in host else host
    return f"http://{rendered_host}:{port}"


class ApplicationConfigurationSettingsSource(PydanticBaseSettingsSource):
    """Read one declared domain from ``configs/config.<domain>.json``."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        self._section = getattr(settings_cls, "configuration_section", None)

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        if self._section is None:
            return {}
        return _load_document(application_configuration_path(self._section))


def _load_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        document = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}.") from error
    if not isinstance(document, dict):
        raise ValueError(f"Configuration document {path} must be an object.")
    return cast(dict[str, Any], document)


def _configuration_host(value: object) -> str:
    if not isinstance(value, str) or not (host := value.strip()):
        raise ValueError("Katalog API host must be a non-blank string.")
    return host


def _configuration_port(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("Katalog API port must be an integer.")
    if isinstance(value, int):
        port = value
    elif isinstance(value, str):
        try:
            port = int(value)
        except ValueError as error:
            raise ValueError("Katalog API port must be an integer.") from error
    else:
        raise ValueError("Katalog API port must be an integer.")
    if not 1 <= port <= 65535:
        raise ValueError("Katalog API port must be between 1 and 65535.")
    return port
