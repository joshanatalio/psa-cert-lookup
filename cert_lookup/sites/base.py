"""SiteDriver protocol shared by all site drivers."""

from __future__ import annotations

from typing import Protocol

from playwright.async_api import Page


class SiteDriver(Protocol):
    name: str

    def __init__(self, page: Page) -> None: ...

    async def search(self, cert: str) -> None:
        """Drive this site to show results for the given cert number."""
        ...
