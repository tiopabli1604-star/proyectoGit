"""
Señales de momentum y reversión de precio.

Los mercados de predicción exhiben momentum a corto plazo (flujo de información)
y reversión a medio plazo (corrección de sobrereacción).
"""

import numpy as np
from typing import Optional


def _prices_from_trades(trades: list[dict]) -> np.ndarray:
    return np.array([float(t["price"]) for t in trades])


def rsi(trades: list[dict], period: int = 14) -> float:
    """RSI adaptado a mercados de predicción (precios en [0,1])."""
    prices = _prices_from_trades(trades)
    if len(prices) < period + 1:
        return 0.5

    deltas = np.diff(prices[:period + 1])
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = gains.mean()
    avg_loss = losses.mean()

    if avg_loss == 0:
        return 1.0
    rs = avg_gain / avg_loss
    return float(1 - 1 / (1 + rs))     # normalizado a [0,1]


def price_momentum(trades: list[dict], short: int = 10,
                   long: int = 50) -> float:
    """
    Momentum: diferencia entre media corta y larga de precio.
    Positivo → tendencia alcista, negativo → bajista.
    Retorna valor en [-1, 1].
    """
    prices = _prices_from_trades(trades)
    if len(prices) < long:
        return 0.0

    ma_short = prices[:short].mean()
    ma_long  = prices[:long].mean()

    # Normaliza por precio base para comparabilidad
    diff = (ma_short - ma_long) / (ma_long + 1e-9)
    return float(np.clip(diff * 5, -1, 1))


def price_volatility(trades: list[dict], window: int = 50) -> float:
    """
    Volatilidad relativa. Alta volatilidad = incertidumbre = señal débil.
    Retorna valor en [0, 1]: 0 = estable, 1 = muy volátil.
    """
    prices = _prices_from_trades(trades)
    if len(prices) < window:
        return 0.5

    std = prices[:window].std()
    # Normaliza: std típica en mercados de predicción ≈ 0.05-0.15
    return float(np.clip(std / 0.15, 0, 1))


def mean_reversion_signal(trades: list[dict], window: int = 100) -> float:
    """
    Señal de reversión a la media.
    Detecta cuando el precio se aleja demasiado de su media histórica.
    Retorna corrección esperada en [-1, 1]: positivo = espera rebote alcista.
    """
    prices = _prices_from_trades(trades)
    if len(prices) < window:
        return 0.0

    mean = prices[:window].mean()
    std  = prices[:window].std()
    last = prices[0]

    if std == 0:
        return 0.0

    z_score = (last - mean) / std
    # Z-score alto negativo → precio muy bajo → espera rebote
    return float(np.clip(-z_score / 3, -1, 1))


def time_decay_signal(end_date_str: Optional[str],
                      current_price: float) -> float:
    """
    Ajuste por time decay: a medida que se acerca la resolución,
    precios extremos (0.05, 0.95) se vuelven más creíbles.
    Penaliza apostar contra el precio dominante cerca del cierre.
    """
    if end_date_str is None:
        return 0.0

    from datetime import datetime, timezone
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        days_left = (end - now).total_seconds() / 86400
    except Exception:
        return 0.0

    if days_left <= 0:
        return 0.0

    urgency = np.clip(1 / (days_left + 1), 0, 1)   # más urgente = más cerca del cierre

    # Si el precio ya es extremo y vence pronto, refuerza esa señal
    if current_price > 0.80:
        return float(urgency * 0.5)
    elif current_price < 0.20:
        return float(-urgency * 0.5)
    return 0.0


def momentum_score(trades: list[dict], end_date: Optional[str] = None) -> float:
    """
    Score compuesto de momentum en [0, 1].
    1 = señal fuerte alcista, 0 = señal fuerte bajista, 0.5 = neutral.
    """
    if not trades:
        return 0.5

    current_price = float(trades[0]["price"]) if trades else 0.5

    rsi_val    = rsi(trades)                                # [0, 1]
    mom_val    = (price_momentum(trades) + 1) / 2          # [-1,1] → [0,1]
    vol_val    = price_volatility(trades)                   # [0, 1] penaliza señal
    rev_val    = (mean_reversion_signal(trades) + 1) / 2   # [-1,1] → [0,1]
    decay_val  = (time_decay_signal(end_date, current_price) + 0.5)  # ≈ [0, 1]

    confidence = 1 - vol_val  # baja volatilidad = más confianza

    raw = (
        0.30 * rsi_val +
        0.25 * mom_val +
        0.25 * rev_val +
        0.20 * decay_val
    ) * confidence + 0.5 * (1 - confidence)  # degrada a 0.5 cuando hay alta volatilidad

    return float(np.clip(raw, 0, 1))
