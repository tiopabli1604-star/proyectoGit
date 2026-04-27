"""
Análisis de flujo de órdenes (Order Flow Analysis).

Detecta desequilibrios entre compradores y vendedores para anticipar
movimientos de precio antes de que el mercado los descuente.
"""

import numpy as np
from typing import Optional


def order_imbalance(orderbook: dict, depth_levels: int = 5) -> float:
    """
    Calcula el desequilibrio bid/ask en los N primeros niveles.
    Retorna valor en [-1, 1]: positivo → presión compradora, negativo → vendedora.
    """
    bids = orderbook.get("bids", [])[:depth_levels]
    asks = orderbook.get("asks", [])[:depth_levels]

    bid_vol = sum(float(b["size"]) for b in bids)
    ask_vol = sum(float(a["size"]) for a in asks)
    total = bid_vol + ask_vol

    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def vwap_signal(trades: list[dict], window: int = 50) -> float:
    """
    Compara el precio actual con el VWAP de las últimas `window` operaciones.
    Retorna la desviación normalizada respecto al VWAP [-1, 1].
    """
    if not trades:
        return 0.0

    recent = trades[:window]
    prices = np.array([float(t["price"]) for t in recent])
    sizes  = np.array([float(t["size"])  for t in recent])

    if sizes.sum() == 0:
        return 0.0

    vwap = np.dot(prices, sizes) / sizes.sum()
    last_price = prices[0]

    # Normaliza la desviación al rango del precio (0-1)
    deviation = (last_price - vwap) / (vwap + 1e-9)
    return float(np.clip(deviation * 10, -1, 1))  # escala × 10 para sensibilidad


def trade_velocity(trades: list[dict], short_window: int = 10,
                   long_window: int = 50) -> float:
    """
    Aceleración del flujo: ratio entre velocidad reciente y velocidad base.
    Valores > 1 indican actividad creciente (señal de información).
    Retorna log-ratio en [-1, 1].
    """
    if len(trades) < long_window:
        return 0.0

    short_vol = sum(float(t["size"]) for t in trades[:short_window])
    long_vol  = sum(float(t["size"]) for t in trades[:long_window])

    avg_short = short_vol / short_window
    avg_long  = long_vol  / long_window

    if avg_long == 0:
        return 0.0

    ratio = avg_short / avg_long
    return float(np.clip(np.log(ratio + 1e-9), -1, 1))


def aggressor_ratio(trades: list[dict], window: int = 50) -> float:
    """
    Proporción de trades iniciados por compradores agresivos (taker buys).
    Polymarket tags trades con 'maker_asset_filled' / 'taker_asset_filled'.
    Retorna valor en [0, 1]: >0.5 = más compradores agresivos.
    """
    if not trades:
        return 0.5

    recent = trades[:window]
    buy_vol = sell_vol = 0.0

    for t in recent:
        side = t.get("side", "").upper()
        size = float(t.get("size", 0))
        if side in ("BUY", "YES"):
            buy_vol += size
        else:
            sell_vol += size

    total = buy_vol + sell_vol
    if total == 0:
        return 0.5
    return buy_vol / total


def orderflow_score(orderbook: dict, trades: list[dict]) -> float:
    """
    Score compuesto de flujo de órdenes en [0, 1].
    0 = presión vendedora máxima, 1 = presión compradora máxima.
    """
    imb      = order_imbalance(orderbook)          # [-1, 1]
    vwap_sig = vwap_signal(trades)                 # [-1, 1]
    velocity = trade_velocity(trades)              # [-1, 1]
    aggr     = aggressor_ratio(trades)             # [0, 1] → escala a [-1, 1]

    aggr_scaled = (aggr - 0.5) * 2                # [0,1] → [-1,1]

    raw = (
        0.40 * imb +
        0.25 * vwap_sig +
        0.20 * velocity +
        0.15 * aggr_scaled
    )
    return float(np.clip((raw + 1) / 2, 0, 1))    # normaliza a [0, 1]
