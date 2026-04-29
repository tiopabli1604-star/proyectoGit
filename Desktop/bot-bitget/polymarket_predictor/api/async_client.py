"""
Cliente asíncrono para Polymarket APIs.

Usa aiohttp para hacer múltiples peticiones en paralelo.
Resultado: escanear 2000 mercados pasa de ~2 min a ~12 segundos.

Cómo funciona:
  - La Gamma API tiene paginación (100 mercados/página)
  - Antes: página 1 → espera → página 2 → espera → ...
  - Ahora: página 1, 2, 3... 20 todas a la vez → espera una sola vez

Uso:
  import asyncio
  from api.async_client import fetch_all_markets_async
  markets = asyncio.run(fetch_all_markets_async(keywords=["bitcoin"]))
"""

import asyncio
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

GAMMA_API_URL = "https://gamma-api.polymarket.com"
MAX_CONCURRENT = 10          # máximo de peticiones simultáneas
REQUEST_TIMEOUT = 12         # segundos por petición
RATE_LIMIT_DELAY = 0.1       # segundos entre lotes


async def _fetch_page(session, url: str, params: dict) -> list[dict]:
    """Fetch una sola página de la Gamma API."""
    try:
        async with session.get(url, params=params,
                               timeout=REQUEST_TIMEOUT) as resp:
            if resp.status != 200:
                return []
            data = await resp.json(content_type=None)
            if isinstance(data, list):
                return data
            return data.get("data", data.get("markets", []))
    except Exception as e:
        logger.debug(f"Async fetch error {url} {params}: {e}")
        return []


async def _fetch_all_pages(base_url: str, endpoint: str,
                           extra_params: dict,
                           max_pages: int,
                           page_size: int = 100) -> list[dict]:
    """
    Descarga todas las páginas de un endpoint en paralelo.
    Estrategia:
      1. Descarga las primeras 5 páginas en paralelo
      2. Si la última página tiene < page_size items, para
      3. Si no, descarga el siguiente lote de 5
    """
    try:
        import aiohttp
    except ImportError:
        logger.warning("aiohttp no instalado — usando cliente síncrono como fallback")
        return []

    all_items: list[dict] = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async def fetch_with_sem(session, page: int) -> list[dict]:
        async with semaphore:
            params = {**extra_params, "limit": page_size, "offset": page * page_size}
            return await _fetch_page(session, f"{base_url}{endpoint}", params)

    connector = aiohttp.TCPConnector(limit=MAX_CONCURRENT, ssl=False)
    headers   = {"User-Agent": "PolymarketBot/2.0"}

    async with aiohttp.ClientSession(connector=connector,
                                     headers=headers) as session:
        batch_size = 5
        page = 0
        while page < max_pages:
            pages_to_fetch = list(range(page, min(page + batch_size, max_pages)))
            tasks = [fetch_with_sem(session, p) for p in pages_to_fetch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            got_partial = False
            for result in results:
                if isinstance(result, Exception):
                    continue
                all_items.extend(result)
                if len(result) < page_size:
                    got_partial = True

            if got_partial:
                break

            page += batch_size
            if page < max_pages:
                await asyncio.sleep(RATE_LIMIT_DELAY)

    return all_items


async def fetch_all_markets_async(keywords: list[str] = None,
                                  max_pages: int = 20) -> list[dict]:
    """
    Descarga todos los mercados activos de Polymarket en paralelo.
    Hasta 10x más rápido que la versión síncrona.
    """
    t0 = time.time()
    params = {"active": "true", "closed": "false"}
    markets = await _fetch_all_pages(GAMMA_API_URL, "/markets",
                                     params, max_pages)

    if keywords:
        kw_lower = [k.lower() for k in keywords]
        markets = [
            m for m in markets
            if any(kw in (m.get("question", "") + " " +
                          m.get("description", "")).lower()
                   for kw in kw_lower)
        ]

    elapsed = time.time() - t0
    logger.info(f"Async fetch: {len(markets)} mercados en {elapsed:.1f}s")
    return markets


async def fetch_all_events_async(keywords: list[str] = None,
                                 max_pages: int = 30) -> list[dict]:
    """
    Descarga mercados vía endpoint de EVENTOS en paralelo.
    Accede a sub-mercados no disponibles en /markets.
    """
    t0 = time.time()
    params = {"active": "true", "closed": "false"}
    events = await _fetch_all_pages(GAMMA_API_URL, "/events",
                                    params, max_pages)

    kw_lower = [k.lower() for k in keywords] if keywords else []
    all_markets: list[dict] = []

    for event in events:
        title = (event.get("title") or "").lower()
        desc  = (event.get("description") or "").lower()
        for m in event.get("markets", []):
            question = (m.get("question") or "").lower()
            text = question + " " + title + " " + desc
            if not kw_lower or any(kw in text for kw in kw_lower):
                m.setdefault("_event_title", event.get("title", ""))
                all_markets.append(m)

    elapsed = time.time() - t0
    logger.info(f"Async events fetch: {len(all_markets)} mercados en {elapsed:.1f}s")
    return all_markets


def run_async_fetch(keywords: list[str] = None,
                    use_events: bool = False,
                    max_pages: int = 20) -> list[dict]:
    """
    Wrapper síncrono para el fetch asíncrono.
    Llama desde código síncrono como si fuera una función normal.
    """
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        logger.warning("aiohttp no disponible, volviendo a cliente síncrono")
        return []

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        if use_events:
            coro = fetch_all_events_async(keywords=keywords, max_pages=max_pages)
        else:
            coro = fetch_all_markets_async(keywords=keywords, max_pages=max_pages)
        result = loop.run_until_complete(coro)
        loop.close()
        return result
    except Exception as e:
        logger.error(f"run_async_fetch error: {e}")
        return []
