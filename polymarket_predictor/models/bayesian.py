"""
Actualizador Bayesiano para predicciones de mercado.

Parte de una prior (precio de mercado como probabilidad inicial) y actualiza
incrementalmente con cada señal nueva usando el teorema de Bayes.
"""

import numpy as np
from dataclasses import dataclass, field


@dataclass
class BetaDistribution:
    """Distribución Beta para modelar probabilidades en [0,1]."""
    alpha: float = 1.0  # éxitos
    beta:  float = 1.0  # fracasos

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        a, b = self.alpha, self.beta
        n = a + b
        return (a * b) / (n * n * (n + 1))

    @property
    def std(self) -> float:
        return np.sqrt(self.variance)

    @property
    def concentration(self) -> float:
        """α + β: mayor = distribución más concentrada = más certeza."""
        return self.alpha + self.beta

    def update(self, evidence_yes: float, evidence_no: float) -> "BetaDistribution":
        """Actualización conjugada: añade pseudo-observaciones."""
        return BetaDistribution(
            alpha=self.alpha + evidence_yes,
            beta =self.beta  + evidence_no,
        )

    def to_dict(self) -> dict:
        return {
            "alpha": round(self.alpha, 4),
            "beta":  round(self.beta,  4),
            "mean":  round(self.mean,  4),
            "std":   round(self.std,   4),
        }


class BayesianUpdater:
    """
    Actualiza la creencia sobre P(YES) usando señales externas.

    El precio de mercado (0-1) es la prior — refleja el conocimiento colectivo.
    Cada señal nueva (news, orderflow, etc.) es una observación que actualiza la creencia.
    """

    def __init__(self, prior_price: float, concentration: float = 10.0):
        """
        prior_price: precio de mercado actual (0-1) como probabilidad inicial.
        concentration: fuerza de la prior (α+β). Mayor = más peso al mercado.
        """
        prior_price = np.clip(prior_price, 0.01, 0.99)
        self.dist = BetaDistribution(
            alpha=prior_price * concentration,
            beta =(1 - prior_price) * concentration,
        )
        self._history: list[dict] = []

    def update_with_signal(self, signal_value: float, signal_strength: float = 1.0,
                           label: str = "") -> float:
        """
        Actualiza la distribución con una señal en [0,1].
        signal_value: 1.0 = señal YES fuerte, 0.0 = señal NO fuerte.
        signal_strength: peso de la señal (0-2 típicamente).
        Retorna la nueva media de P(YES).
        """
        signal_value = np.clip(signal_value, 0.01, 0.99)
        evidence_yes = signal_value * signal_strength
        evidence_no  = (1 - signal_value) * signal_strength

        old_mean = self.dist.mean
        self.dist = self.dist.update(evidence_yes, evidence_no)
        self._history.append({
            "label": label,
            "signal": round(signal_value, 4),
            "strength": signal_strength,
            "delta": round(self.dist.mean - old_mean, 4),
        })
        return self.dist.mean

    def update_with_multiple(self, signals: dict[str, tuple[float, float]]) -> float:
        """
        signals = {"nombre": (valor_0_1, fuerza), ...}
        Retorna P(YES) final.
        """
        for label, (value, strength) in signals.items():
            self.update_with_signal(value, strength, label)
        return self.dist.mean

    @property
    def probability(self) -> float:
        return self.dist.mean

    @property
    def uncertainty(self) -> float:
        """Incertidumbre en [0,1]: alta cuando std es grande."""
        return float(np.clip(self.dist.std * 4, 0, 1))

    def summary(self) -> dict:
        return {
            "distribution": self.dist.to_dict(),
            "probability":  round(self.probability, 4),
            "uncertainty":  round(self.uncertainty, 4),
            "updates":      self._history,
        }
