"""
Análisis de flujo de órdenes (Order Flow Analysis).

Detecta desequilibrios entre compradores y vendedores para anticipar
movimientos de precio antes de que el mercado los descuente.
"""

import numpy as np
from datetime import datetime, timezone
from typing import Optional


def order_imbalance(orderbook: dict, depth_levels: int = 10) -> float:
    """
    Desequilibrio bid/ask ponderado por profundidad.
    Las órdenes más cercanas al mid tienen más peso.
    Retorna [-1, 1]: positivo → presión compradora.
    """
    bids = orderbook.get("bids", [])[:depth_levels]
    asks = orderbook.get("asks", [])[:depth_levels]

    bid_vol = sum(float(b["size"]) * (1 / (i + 1)) for i, b in enumerate(bids))
    ask_vol = sum(float(a["size"]) * (1 / (i + 1)) for i, a in enumerate(asks))
    total = bid_vol + ask_vol

    if total == 0:
        return 0.0
    return (bid_vol - ask_vol) / total


def orderbook_depth_ratio(orderbook: dict, levels: int = 5) -> float:
    """
    Ratio de volumen total bid vs ask en los N niveles más cercanos.
    Detecta muros de órdenes que el precio tendrá dificultad para cruzar.
    """
    bids = orderbook.get("bids", [])[:levels]
    asks = orderbook.get("asks", [])[:levels]

    bid_vol = sum(float(b["size"]) for b in bids)
    ask_vol = sum(float(a["size"]) for a in asks)

    if ask_vol == 0:
        return 1.0
    if bid_vol == 0:
        return 0.0
    ratio = bid_vol / (bid_vol + ask_vol)
    return float(np.clip(ratio, 0, 1))


def vwap_signal(trades: list[dict], window: int = 50) -> float:
    """
    Desviación del precio actual respecto al VWAP reciente.
    Precio > VWAP → señal alcista. Normalizado a [-1, 1].
    """
    if not trades:
        return 0.0

    recent = trades[:window]
    prices = np.array([float(t.get("price", 0.5)) for t in recent])
    sizes  = np.array([float(t.get("size",  1.0)) for t in recent])

    if sizes.sum() == 0:
        return 0.0

    vwap = np.dot(prices, sizes) / sizes.sum()
    last_price = prices[0]

    deviation = (last_price - vwap) / max(vwap, 1e-9)
    return float(np.clip(deviation * 10, -1, 1))


def trade_velocity(trades: list[dict], short_window: int = 10,
                   long_window: int = 50) -> float:
    """
    Aceleración del volumen: ratio entre actividad reciente y base.
    Valores > 0 indican aumento de interés = información nueva entrando.
    """
    if len(trades) < long_window:
        return 0.0

    short_vol = sum(float(t.get("size", 0)) for t in trades[:short_window])
    long_vol  = sum(float(t.get("size", 0)) for t in trades[:long_window])

    avg_short = short_vol / short_window
    avg_long  = long_vol  / long_window

    if avg_long == 0:
        return 0.0

    ratio = avg_short / avg_long
    return float(np.clip(np.log(ratio + 1e-9), -1, 1))


def aggressor_ratio(trades: list[dict], window: int = 50) -> float:
    """
    Proporción de trades iniciados por compradores agresivos.
    > 0.5 = más compradores que vendedores.
    """
    if not trades:
        return 0.5

    buy_vol = sell_vol = 0.0
    for t in trades[:window]:
        side = t.get("side", "").upper()
        size = float(t.get("size", 0))
        if side in ("BUY", "YES"):
            buy_vol += size
        else:
            sell_vol += size

    total = buy_vol + sell_vol
    return buy_vol / total if total > 0 else 0.5


def market_freshness(trades: list[dict]) -> float:
    """
    ¿Cuándo fue el último trade? Mercados sin actividad reciente
    son MENOS eficientes = mayor oportunidad.
    Retorna [0, 1]: 1 = muy activo ahora, 0 = dormido.

    Nota: un mercado dormido no es malo per se — pero sí señala
    que su precio puede ser más stale.
    """
    if not trades:
        return 0.0

    now = datetime.now(timezone.utc).timestamp()
    for t in trades[:5]:
        ts_raw = t.get("timestamp") or t.get("created_at") or t.get("time")
        if ts_raw is None:
            continue
        try:
            if isinstance(ts_raw, str):
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00")).timestamp()
            else:
                ts = float(ts_raw)
            age_hours = (now - ts) / 3600
            # Decae exponencialmente: 1h=0.97, 6h=0.83, 24h=0.57, 72h=0.22
            return float(np.clip(np.exp(-age_hours / 30), 0, 1))
        except Exception:
            continue
    return 0.5


def large_trade_signal(trades: list[dict], top_n: int = 5,
                       size_threshold: float = 500.0) -> float:
    """
    Detecta si los trades grandes (ballenas) son compradores o vendedores.
    Las ballenas suelen tener información privada → señal valiosa.
    Retorna [-1, 1]: positivo = ballenas comprando YES.
    """
    if not trades:
        return 0.0

    large_trades = [t for t in trades[:50]
                    if float(t.get("size", 0)) >= size_threshold][:top_n]

    if not large_trades:
        return 0.0

    scores = []
    for t in large_trades:
        side = t.get("side", "").upper()
        size = float(t.get("size", 0))
        direction = 1.0 if side in ("BUY", "YES") else -1.0
        scores.append(direction * np.log1p(size))

    total_weight = sum(np.log1p(float(t.get("size", 0))) for t in large_trades)
    if total_weight == 0:
        return 0.0
    return float(np.clip(sum(scores) / total_weight, -1, 1))


def orderflow_score(orderbook: dict, trades: list[dict]) -> float:
    """
    Score compuesto de flujo de órdenes en [0, 1].
    0 = presión vendedora máxima, 1 = presión compradora máxima.
    """
    imb       = order_imbalance(orderbook)           # [-1, 1]
    depth     = (orderbook_depth_ratio(orderbook) - 0.5) * 2  # [0,1]→[-1,1]
    vwap_sig  = vwap_signal(trades)                  # [-1, 1]
    velocity  = trade_velocity(trades)               # [-1, 1]
    aggr      = (aggressor_ratio(trades) - 0.5) * 2 # [0,1]→[-1,1]
    whale     = large_trade_signal(trades)           # [-1, 1]

    raw = (
        0.30 * imb +
        0.15 * depth +
        0.20 * vwap_sig +
        0.15 * velocity +
        0.10 * aggr +
        0.10 * whale
    )
    return float(np.clip((raw + 1) / 2, 0, 1))
