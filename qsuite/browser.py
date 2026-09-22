"""A small pool of headless browser contexts, for engines with no usable API.

Playwright is an optional dependency: everything that can be done over plain
HTTP is, because a browser costs ~20x the memory and is far easier to
fingerprint. Only Qatar's own site really needs one.

The pool exists to satisfy requirement 3 — when a site has no calendar view,
run its per-date queries concurrently instead of sequentially. Each worker gets
its own browser *context* (separate cookie jar and storage), so a challenge
served to one does not poison the rest.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from typing import Any, Optional

log = logging.getLogger(__name__)

#: Cut the most obvious headless tells. This is not a stealth framework and
#: will not defeat a determined Akamai deployment — it just avoids failing on
#: the trivial checks. If a site is actively hunting you, slow down instead.
_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
Object.defineProperty(navigator, 'languages', {get: () => ['en-CA', 'en']});
Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
window.chrome = window.chrome || {runtime: {}};
"""

VIEWPORTS = [(1440, 900), (1512, 982), (1680, 1050), (1366, 768)]


class BrowserUnavailable(RuntimeError):
    """Playwright is not installed, or no browser binary is present."""


class BrowserPool:
    """``size`` independent contexts, handed out under a semaphore."""

    def __init__(self, size: int = 2, *, headless: bool = True,
                 storage_state: Optional[str] = None,
                 proxy: Optional[dict[str, str]] = None,
                 slow_mo_ms: int = 0) -> None:
        self.size = max(1, size)
        self.headless = headless
        self.storage_state = storage_state
        self.proxy = proxy
        self.slow_mo_ms = slow_mo_ms
        self._pw = None
        self._browser = None
        self._contexts: list[Any] = []
        self._free: asyncio.Queue = asyncio.Queue()
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise BrowserUnavailable(
                "playwright is not installed. Install it with "
                "`pip install 'qsuite-scanner[browser]' && playwright install chromium`, "
                "or scan only the HTTP providers (--provider ba,qantas,alaska)."
            ) from exc

        self._pw = await async_playwright().start()
        launch: dict[str, Any] = {
            "headless": self.headless,
            "slow_mo": self.slow_mo_ms,
            "args": ["--disable-blink-features=AutomationControlled",
                     "--disable-dev-shm-usage"],
        }
        if self.proxy:
            launch["proxy"] = self.proxy
        try:
            self._browser = await self._pw.chromium.launch(**launch)
        except Exception as exc:  # noqa: BLE001
            await self._shutdown_pw()
            raise BrowserUnavailable(
                f"could not launch chromium ({exc}). Run `playwright install chromium`."
            ) from exc

        for _ in range(self.size):
            w, h = random.choice(VIEWPORTS)
            ctx_kwargs: dict[str, Any] = {
                "viewport": {"width": w, "height": h},
                "locale": "en-CA",
                "timezone_id": "America/Toronto",
            }
            if self.storage_state:
                ctx_kwargs["storage_state"] = self.storage_state
            context = await self._browser.new_context(**ctx_kwargs)
            await context.add_init_script(_STEALTH_JS)
            self._contexts.append(context)
            self._free.put_nowait(context)
        self._started = True
        log.info("browser pool up: %d context(s), headless=%s", self.size, self.headless)

    @contextlib.asynccontextmanager
    async def page(self):
        """Borrow a context, yield a fresh page, always give the context back."""
        if not self._started:
            await self.start()
        context = await self._free.get()
        page = await context.new_page()
        try:
            yield page
        finally:
            with contextlib.suppress(Exception):
                await page.close()
            self._free.put_nowait(context)

    async def save_state(self, path: str) -> None:
        """Persist cookies from the first context, so a login survives runs."""
        if self._contexts:
            await self._contexts[0].storage_state(path=path)

    async def aclose(self) -> None:
        for c in self._contexts:
            with contextlib.suppress(Exception):
                await c.close()
        self._contexts.clear()
        if self._browser:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        await self._shutdown_pw()
        self._started = False

    async def _shutdown_pw(self) -> None:
        if self._pw:
            with contextlib.suppress(Exception):
                await self._pw.stop()
            self._pw = None


async def human_pause(lo: float = 0.4, hi: float = 1.6) -> None:
    """A short irregular pause, so the click stream is not metronomic."""
    await asyncio.sleep(random.uniform(lo, hi))
