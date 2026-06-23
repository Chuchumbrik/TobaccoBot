"""Фоновый прогрев и периодическое обновление снимков каталога (все сайты)."""

from __future__ import annotations

import asyncio
import logging
import os

from telegram.ext import Application

from bot.handlers.common import SERVICE_KEY
from bot import service_async as osh_async
from oshisha import catalog_cache

logger = logging.getLogger(__name__)


def _refresh_interval_sec() -> float:
    return float(os.environ.get("CATALOG_REFRESH_INTERVAL", "3600"))


def _all_sites_ready(hub) -> bool:
    return all(catalog_cache.is_ready(sid) for sid in hub.site_ids)


async def _warmup_all(hub, *, force: bool) -> dict:
    catalog_cache.set_updating(True)
    try:
        return await osh_async.warmup_all_catalogs(hub, force=force)
    finally:
        catalog_cache.set_updating(False)


async def warmup_catalog_once(application: Application) -> None:
    if not catalog_cache.is_warmup_enabled():
        logger.info("Catalog warmup skipped (CATALOG_WARMUP=0)")
        return

    hub = application.bot_data.get(SERVICE_KEY)
    if hub is None:
        return

    timeout = float(os.environ.get("CATALOG_WARMUP_TIMEOUT", "600"))
    logger.info(
        "Catalog snapshot: background warmup all sites %s (timeout %.0fs)…",
        hub.site_ids,
        timeout,
    )
    try:
        results = await asyncio.wait_for(
            _warmup_all(hub, force=True),
            timeout=timeout,
        )
        for sid, snap in results.items():
            logger.info(
                "Catalog %s ready: %d products, %d sections",
                sid,
                snap.product_count,
                snap.sections_scanned,
            )
    except asyncio.TimeoutError:
        logger.warning("Catalog warmup timed out after %.0fs", timeout)
    except NotImplementedError as exc:
        logger.info("Catalog warmup skipped: %s", exc)
    except Exception as exc:
        logger.warning("Catalog warmup failed (non-fatal): %s", exc)


async def refresh_catalog_if_due(application: Application) -> None:
    hub = application.bot_data.get(SERVICE_KEY)
    if hub is None:
        return
    try:
        results = await _warmup_all(hub, force=False)
        for sid, snap in results.items():
            logger.info(
                "Catalog %s refreshed: %d products",
                sid,
                snap.product_count,
            )
    except NotImplementedError:
        pass
    except Exception as exc:
        logger.warning("Catalog background refresh failed: %s", exc)


async def catalog_background_loop(application: Application) -> None:
    """Первый прогрев всех сайтов, затем refresh раз в CATALOG_REFRESH_INTERVAL."""
    await warmup_catalog_once(application)
    interval = _refresh_interval_sec()
    if interval <= 0:
        logger.info("Catalog background refresh disabled (CATALOG_REFRESH_INTERVAL=0)")
        return

    logger.info("Catalog background refresh every %.0fs", interval)
    hub = application.bot_data.get(SERVICE_KEY)
    while True:
        try:
            await asyncio.sleep(interval)
            if hub is None:
                hub = application.bot_data.get(SERVICE_KEY)
            if hub is None:
                continue
            if not _all_sites_ready(hub):
                await warmup_catalog_once(application)
                continue
            await refresh_catalog_if_due(application)
        except asyncio.CancelledError:
            logger.info("catalog_background_loop: cancelled, stopping")
            raise
        except Exception:
            logger.exception("catalog_background_loop: unexpected error, будет retry через %.0fs", interval)


def start_catalog_background(application: Application) -> None:
    """Не блокирует post_init — задача в event loop."""
    if not catalog_cache.is_warmup_enabled():
        return
    asyncio.create_task(
        catalog_background_loop(application),
        name="catalog_background",
    )
