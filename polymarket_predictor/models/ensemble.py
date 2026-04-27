"""
Modelo Ensemble: combina todas las señales en una predicción final.

Usa XGBoost como meta-aprendiz sobre las señales base cuando hay datos históricos
disponibles, y cae back a una combinación lineal ponderada si no hay modelo entrenado.
"""

import os
import pickle
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False

from models.calibration import ensemble_calibration, confidence_interval
from config import SIGNAL_WEIGHTS


@dataclass
class SignalBundle:
    """Contenedor de todas las señales para un mercado."""
    market_price:   float = 0.5   # precio actual del mercado (prior)
    orderflow:      float = 0.5   # señal de flujo de órdenes [0,1]
    momentum:       float = 0.5   # señal de momentum [0,1]
    calibrated:     float = 0.5   # precio calibrado históricamente [0,1]
    bayesian:       float = 0.5   # probabilidad bayesiana [0,1]
    uncertainty:    float = 0.5   # incertidumbre [0,1]
    spread:         float = 0.05  # spread bid-ask
    volume_24h:     float = 1000  # volumen 24h USD

    def to_features(self) -> np.ndarray:
        return np.array([
            self.market_price,
            self.orderflow,
            self.momentum,
            self.calibrated,
            self.bayesian,
            self.uncertainty,
            self.spread,
            np.log1p(self.volume_24h),
            # Features derivadas
            abs(self.market_price - 0.5),          # distancia al centro
            self.orderflow - self.momentum,         # divergencia OF vs momentum
            self.bayesian - self.market_price,      # edge bayesiano
            self.calibrated - self.market_price,    # edge de calibración
        ], dtype=np.float32)


@dataclass
class Prediction:
    """Resultado de predicción con metadata de confianza."""
    probability:    float          # P(YES) estimada
    market_price:   float          # precio actual del mercado
    edge:           float          # diferencia con el mercado (+ = YES tiene valor)
    ci_lower:       float          # límite inferior IC 95%
    ci_upper:       float          # límite superior IC 95%
    confidence:     float          # confianza de la señal [0,1]
    signals:        dict = field(default_factory=dict)
    recommendation: str = ""

    def __post_init__(self):
        self.recommendation = self._recommend()

    def _recommend(self) -> str:
        if self.confidence < 0.4:
            return "SKIP — señal débil"
        if self.edge > 0.05:
            return f"BUY YES @ {self.market_price:.2f} (edge +{self.edge:.1%})"
        if self.edge < -0.05:
            return f"BUY NO @ {1-self.market_price:.2f} (edge +{abs(self.edge):.1%})"
        return "HOLD — sin edge suficiente"


class EnsembleModel:
    """
    Meta-modelo que combina señales en una predicción final.

    Modo lineal: promedio ponderado en espacio logit (siempre disponible).
    Modo XGBoost: meta-aprendiz entrenado sobre datos históricos (opcional).
    """

    MODEL_PATH = "polymarket_predictor/model_cache/ensemble_xgb.pkl"

    def __init__(self):
        self._xgb_model = self._load_xgb()

    def _load_xgb(self):
        if not XGB_AVAILABLE:
            return None
        if os.path.exists(self.MODEL_PATH):
            with open(self.MODEL_PATH, "rb") as f:
                return pickle.load(f)
        return None

    def save_xgb(self, model):
        os.makedirs(os.path.dirname(self.MODEL_PATH), exist_ok=True)
        with open(self.MODEL_PATH, "wb") as f:
            pickle.dump(model, f)
        self._xgb_model = model

    def train_xgb(self, X: np.ndarray, y: np.ndarray):
        """
        Entrena el meta-aprendiz XGBoost.
        X: matriz de features (SignalBundle.to_features() por cada mercado).
        y: etiquetas binarias (1 = YES ganó, 0 = NO ganó).
        Requiere al menos 100 mercados resueltos para ser útil.
        """
        if not XGB_AVAILABLE:
            raise ImportError("xgboost no instalado. Ejecuta: pip install xgboost")

        model = xgb.XGBClassifier(
            n_estimators=200,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            use_label_encoder=False,
            eval_metric="logloss",
            random_state=42,
        )
        model.fit(X, y, eval_set=[(X, y)], verbose=False)
        self.save_xgb(model)
        return model

    def _linear_predict(self, signals: SignalBundle) -> float:
        """Combinación lineal ponderada en espacio logit."""
        preds = {
            "orderflow":   signals.orderflow,
            "momentum":    signals.momentum,
            "calibration": signals.calibrated,
            "bayesian":    signals.bayesian,
        }
        return ensemble_calibration(preds, SIGNAL_WEIGHTS)

    def predict(self, signals: SignalBundle) -> Prediction:
        """Genera predicción final a partir de las señales."""
        if self._xgb_model is not None and XGB_AVAILABLE:
            feats = signals.to_features().reshape(1, -1)
            prob = float(self._xgb_model.predict_proba(feats)[0, 1])
        else:
            prob = self._linear_predict(signals)

        uncertainty_raw = signals.uncertainty
        ci_lo, ci_hi = confidence_interval(prob, uncertainty_raw * 0.3)

        confidence = 1 - uncertainty_raw
        edge = prob - signals.market_price

        return Prediction(
            probability  = round(prob, 4),
            market_price = round(signals.market_price, 4),
            edge         = round(edge, 4),
            ci_lower     = round(ci_lo, 4),
            ci_upper     = round(ci_hi, 4),
            confidence   = round(confidence, 4),
            signals={
                "orderflow":   round(signals.orderflow, 4),
                "momentum":    round(signals.momentum, 4),
                "calibrated":  round(signals.calibrated, 4),
                "bayesian":    round(signals.bayesian, 4),
            },
        )
