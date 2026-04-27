"""
Calibración de probabilidades históricas.

Los mercados de predicción suelen presentar sesgos sistemáticos:
- Overpricing de eventos de baja probabilidad (efecto lotería)
- Underpricing de eventos de alta probabilidad (conservadurismo)
- Sesgos políticos o de atención mediática

Este módulo aplica correcciones basadas en patrones históricos conocidos.
"""

import numpy as np
from scipy.special import expit, logit


def platt_scaling(raw_prob: float, a: float = 1.0, b: float = 0.0) -> float:
    """
    Calibración de Platt: aplica una transformación sigmoidea sobre el logit.
    Parámetros a y b se aprenderían del historial de mercados resueltos.
    a < 1 → comprime probabilidades extremas (útil para overconfidence).
    """
    raw_prob = np.clip(raw_prob, 0.01, 0.99)
    calibrated = expit(a * logit(raw_prob) + b)
    return float(np.clip(calibrated, 0.01, 0.99))


# Coeficientes empíricos por tipo de mercado
# Estimados sobre análisis de mercados resueltos en Polymarket
CALIBRATION_PARAMS = {
    "politics":  {"a": 0.85, "b": -0.05},   # overconfident en eventos políticos
    "sports":    {"a": 0.92, "b":  0.00},   # bien calibrado generalmente
    "crypto":    {"a": 0.80, "b":  0.10},   # underpricing de YES en bull markets
    "economics": {"a": 0.88, "b": -0.02},
    "default":   {"a": 0.90, "b":  0.00},
}


def calibrate_probability(raw_prob: float,
                          market_type: str = "default") -> float:
    """Aplica calibración específica por tipo de mercado."""
    params = CALIBRATION_PARAMS.get(market_type, CALIBRATION_PARAMS["default"])
    return platt_scaling(raw_prob, params["a"], params["b"])


def liquidity_adjustment(raw_prob: float, spread: float,
                         volume_24h: float) -> float:
    """
    Ajusta la probabilidad según la liquidez del mercado.
    Mercados ilíquidos tienen precios menos informativos.
    Degrada la señal hacia 0.5 cuanto menos líquido sea el mercado.
    """
    # Factor de liquidez: 1.0 = muy líquido, 0.0 = muy ilíquido
    spread_factor  = np.clip(1 - spread / 0.15, 0, 1)       # spread > 15% → poco fiable
    volume_factor  = np.clip(np.log1p(volume_24h) / 10, 0, 1)  # escala logarítmica

    liquidity = 0.5 * spread_factor + 0.5 * volume_factor
    return float(0.5 + (raw_prob - 0.5) * liquidity)


def ensemble_calibration(predictions: dict[str, float],
                         weights: dict[str, float]) -> float:
    """
    Combina predicciones de múltiples modelos en espacio logit
    (más correcto estadísticamente que promediar probabilidades directas).

    predictions: {"modelo": prob, ...}
    weights: {"modelo": weight, ...}
    """
    assert set(predictions.keys()) == set(weights.keys()), \
        "predictions y weights deben tener las mismas claves"

    total_weight = sum(weights.values())
    logit_sum = sum(
        weights[k] * logit(np.clip(v, 0.01, 0.99))
        for k, v in predictions.items()
    )
    blended_logit = logit_sum / total_weight
    return float(expit(blended_logit))


def confidence_interval(prob: float, uncertainty: float,
                        z: float = 1.96) -> tuple[float, float]:
    """
    Intervalo de confianza aproximado en espacio logit.
    uncertainty: desviación estándar estimada de la predicción.
    z: 1.96 → 95% CI, 1.645 → 90% CI.
    """
    logit_p = logit(np.clip(prob, 0.01, 0.99))
    margin  = z * uncertainty

    lower = float(expit(logit_p - margin))
    upper = float(expit(logit_p + margin))
    return lower, upper
