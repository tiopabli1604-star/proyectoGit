"""
Calibración por tasas históricas de resolución de Polymarket.

Descarga mercados ya resueltos y calcula cuántas veces el mercado
tenía razón a distintos niveles de precio.

Si un mercado al 70% resuelve YES el 65% de las veces → está sobreestimado.
Si un mercado al 20% resuelve YES el 30% de las veces → está infravalorado.

Esta curva de calibración corrige el precio de mercado antes del ensemble.
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import requests
import numpy as np

logger = logging.getLogger(__name__)

GAMMA_API   = "https://gamma-api.polymarket.com"
CACHE_FILE  = Path("base_rates_cache.json")
CACHE_TTL   = 86400   # 24 horas

_session = requests.Session()
_session.headers["User-Agent"] = "PolymarketBot/2.0"


def _load_cache() -> Optional[dict]:
    if CACHE_FILE.exists():
        try:
            data = json.loads(CACHE_FILE.read_text())
            if time.time() - data.get("ts", 0) < CACHE_TTL:
                return data
        except Exception:
            pass
    return None


def _save_cache(data: dict):
    data["ts"] = time.time()
    CACHE_FILE.write_text(json.dumps(data))


def fetch_resolved_markets(max_pages: int = 10) -> list[dict]:
    """Descarga mercados resueltos (cerrados) de Polymarket."""
    markets = []
    for page in range(max_pages):
        try:
            r = _session.get(f"{GAMMA_API}/markets", params={
                "limit":  100,
                "offset": page * 100,
                "closed": "true",
                "active": "false",
            }, timeout=10)
            r.raise_for_status()
            batch = r.json()
            if isinstance(batch, dict):
                batch = batch.get("data", [])
            if not batch:
                break
            markets.extend(batch)
            if len(batch) < 100:
                break
        except Exception as e:
            logger.debug(f"Error fetching resolved markets page {page}: {e}")
            break
    return markets


def build_calibration_curve(markets: list[dict]) -> dict:
    """
    Construye curva de calibración por buckets de precio.
    Retorna dict: {bucket_center: resolution_rate}
    Buckets: 0.05, 0.15, 0.25, ..., 0.95
    """
    buckets = {i/10: [] for i in range(1, 10)}  # 0.1, 0.2, ..., 0.9
    # Usamos también resolución: si outcome[0]=YES y resolutionIndex=0 → YES ganó

    for m in markets:
        prices = m.get("outcomePrices", [])
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except Exception:
                continue

        resolution = m.get("resolutionIndex")
        if resolution is None:
            # intenta inferir del winner
            outcomes = m.get("outcomes", [])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []
            winner = m.get("winner") or m.get("question_winner")
            if outcomes and winner:
                try:
                    resolution = outcomes.index(winner) if winner in outcomes else None
                except Exception:
                    resolution = None

        if resolution is None or not prices:
            continue

        try:
            yes_price = float(prices[0])
            yes_won = int(resolution) == 0
        except (ValueError, IndexError, TypeError):
            continue

        if not (0.01 <= yes_price <= 0.99):
            continue

        # Bucket más cercano (0.1, 0.2, ..., 0.9)
        bucket = round(round(yes_price / 0.1) * 0.1, 1)
        bucket = max(0.1, min(0.9, bucket))
        if bucket in buckets:
            buckets[bucket].append(1 if yes_won else 0)

    curve = {}
    for b, outcomes in buckets.items():
        if len(outcomes) >= 10:   # mínimo 10 mercados para fiarse
            curve[str(b)] = round(sum(outcomes) / len(outcomes), 4)
            logger.debug(f"Bucket {b:.1f}: {curve[str(b)]:.1%} ({len(outcomes)} mercados)")

    return curve


def get_calibration_curve() -> dict:
    """Retorna la curva de calibración (con caché de 24h)."""
    cached = _load_cache()
    if cached and cached.get("curve"):
        return cached["curve"]

    logger.info("Calculando base rates desde mercados resueltos...")
    markets = fetch_resolved_markets(max_pages=15)
    logger.info(f"Mercados resueltos obtenidos: {len(markets)}")

    if len(markets) < 50:
        return {}

    curve = build_calibration_curve(markets)
    if curve:
        _save_cache({"curve": curve})
        logger.info(f"Curva de calibración: {curve}")
    return curve


def apply_base_rate(market_price: float, curve: dict) -> float:
    """
    Aplica la corrección de base rate al precio de mercado.
    Si el mercado históricamente acierta bien → pequeña corrección.
    Si el mercado está sistemáticamente sesgado → corrección mayor.
    """
    if not curve:
        return market_price

    # Encuentra el bucket más cercano
    bucket = round(round(market_price / 0.1) * 0.1, 1)
    bucket = max(0.1, min(0.9, bucket))
    key = str(bucket)

    if key not in curve:
        return market_price

    historical_rate = curve[key]
    # Corrección suave: promedio ponderado 70% mercado + 30% histórico
    corrected = 0.70 * market_price + 0.30 * historical_rate
    return round(corrected, 4)
