"""Typed operational errors raised by Kourier providers."""

from __future__ import annotations

from kasana.shared.metadata import ProviderErrorCategory


class KourierError(RuntimeError):
    def __init__(
        self,
        category: ProviderErrorCategory,
        message: str,
        *,
        provider: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.provider = provider
        self.status_code = status_code
