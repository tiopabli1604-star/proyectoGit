"""
Gestión de riesgo: Kelly Criterion fraccionado.

El Kelly Criterion calcula la fracción óptima del bankroll a apostar
para maximizar el crecimiento logarítmico a largo plazo.

Usamos Kelly fraccionado (25%) para reducir varianza y drawdown.
"""

import numpy as np
from dataclasses import dataclass
from config import KELLY_FRACTION, MAX_POSITION_USD, MAX_PORTFOLIO_EXPOSURE


@dataclass
class PositionSize:
    kelly_fraction: float      # Kelly teórico [0, 1]
    fractional_kelly: float    # Kelly fraccionado
    usd_amount: float          # Monto en USD
    bankroll_pct: float        # % del bankroll
    rationale: str             # Explicación del sizing


def kelly_fraction(prob: float, odds: float) -> float:
    """
    Calcula la fracción Kelly para una apuesta binaria.

    prob: probabilidad estimada de ganar (0-1)
    odds: cuánto ganas por cada $1 apostado (Polymarket: 1/price - 1)

    f* = (p * b - q) / b
    donde b = odds (ganancia neta), p = prob de ganar, q = 1-p
    """
    q = 1 - prob
    if odds <= 0:
        return 0.0
    f = (prob * odds - q) / odds
    return float(max(0, f))


def position_size(
    prob: float,
    market_price: float,
    bankroll: float,
    current_exposure: float = 0.0,
    confidence: float = 1.0,
) -> PositionSize:
    """
    Calcula el tamaño óptimo de posición.

    prob: probabilidad estimada P(YES)
    market_price: precio actual del token YES (0-1)
    bankroll: capital total disponible en USD
    current_exposure: exposición actual del portafolio (USD)
    confidence: factor de confianza de la señal [0,1]
    """
    edge = prob - market_price

    if edge <= 0:
        return PositionSize(0, 0, 0, 0, "Sin edge positivo — no apostar")

    # Odds de Polymarket: si compras YES a 0.30, ganas 1/0.30 - 1 = 2.33x
    odds = (1 / market_price) - 1

    raw_kelly = kelly_fraction(prob, odds)

    # Ajusta por confianza de señal
    adjusted_kelly = raw_kelly * confidence

    # Kelly fraccionado
    frac_kelly = adjusted_kelly * KELLY_FRACTION

    # Límite por exposición máxima del portafolio
    max_by_exposure = max(0, MAX_PORTFOLIO_EXPOSURE * bankroll - current_exposure)

    usd_raw = frac_kelly * bankroll
    usd_capped = min(usd_raw, MAX_POSITION_USD, max_by_exposure)

    bankroll_pct = usd_capped / bankroll if bankroll > 0 else 0

    reason = (
        f"Kelly={raw_kelly:.1%} → fraccionado={frac_kelly:.1%} → "
        f"${usd_capped:.2f} ({bankroll_pct:.1%} del bankroll)"
    )

    return PositionSize(
        kelly_fraction    = round(raw_kelly, 4),
        fractional_kelly  = round(frac_kelly, 4),
        usd_amount        = round(usd_capped, 2),
        bankroll_pct      = round(bankroll_pct, 4),
        rationale         = reason,
    )


def expected_value(prob: float, market_price: float, amount: float) -> dict:
    """
    Calcula EV, ganancia esperada y ratio riesgo/recompensa.
    """
    odds = (1 / market_price) - 1
    win_amount  = amount * odds
    lose_amount = amount

    ev = prob * win_amount - (1 - prob) * lose_amount
    roi = ev / amount if amount > 0 else 0

    return {
        "ev":            round(ev, 4),
        "roi":           round(roi, 4),
        "win_amount":    round(win_amount, 4),
        "lose_amount":   round(lose_amount, 4),
        "risk_reward":   round(win_amount / lose_amount, 4) if lose_amount > 0 else 0,
    }


def portfolio_risk_check(positions: list[dict], bankroll: float) -> dict:
    """
    Evalúa el riesgo global del portafolio.
    positions: lista de {"market_id": str, "usd": float, "prob": float}
    """
    total_exposure = sum(p["usd"] for p in positions)
    max_loss = total_exposure  # peor caso: todas las apuestas pierden

    # Diversificación: penaliza concentración en pocos mercados
    if len(positions) > 0:
        weights = np.array([p["usd"] for p in positions]) / total_exposure
        herfindahl = float(np.sum(weights ** 2))  # 1/N = diversificado, 1 = concentrado
    else:
        herfindahl = 1.0

    return {
        "total_exposure_usd":  round(total_exposure, 2),
        "total_exposure_pct":  round(total_exposure / bankroll, 4) if bankroll else 0,
        "max_loss_usd":        round(max_loss, 2),
        "num_positions":       len(positions),
        "concentration_hhi":   round(herfindahl, 4),
        "is_safe": (
            total_exposure / bankroll < MAX_PORTFOLIO_EXPOSURE if bankroll else False
        ),
    }
