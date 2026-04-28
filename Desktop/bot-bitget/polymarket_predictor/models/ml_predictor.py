"""
Modelo ML (XGBoost) entrenado en datos históricos de Polymarket.

Entrena un calibrador que aprende cuándo el precio de mercado es exacto
o está sesgado, basándose en features observables:
  - precio de mercado
  - categoría (crypto/política/elecciones/finanzas)
  - volumen 24h
  - liquidez
  - spread
  - días hasta resolución
  - score de orderflow
  - score de momentum

Datos de entrenamiento: mercados resueltos de Polymarket Gamma API.
El modelo aprende:
  P(resolución_YES | precio_mercado, categoría, volumen, días_left, ...)

Esto es una mejora sobre la calibración por buckets (base_rates.py) porque:
  1. Es no-lineal (las interacciones entre features importan)
  2. Usa más features (no solo el precio)
  3. Por ejemplo: mercado crypto al 80% con volumen bajo y 60 días = más incertidumbre
     que mercado de elecciones al 80% con mucho volumen y 3 días
"""

import json
import logging
import time
import math
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

MODEL_FILE   = Path("data/ml_model.pkl")
SCALER_FILE  = Path("data/ml_scaler.pkl")
CACHE_FILE   = Path("data/ml_training_data.json")
MIN_SAMPLES  = 200      # mínimo para entrenar
MODEL_TTL    = 86400 * 7  # re-entrenar cada 7 días

_CATEGORY_IDS = {
    "crypto":     0,
    "elecciones": 1,
    "politica":   2,
    "finanzas":   3,
    "default":    4,
}


def _extract_features(market_price: float, category: str,
                      volume_24h: float, liquidity: float,
                      spread: float, days_left: Optional[float],
                      orderflow: float = 0.5,
                      momentum: float = 0.5) -> list[float]:
    """
    Extrae vector de features para el modelo ML.
    Aplica transformaciones para normalizar escalas.
    """
    cat_id = _CATEGORY_IDS.get(category, 4)

    # Transformaciones para reducir skewness
    log_vol  = math.log(1 + volume_24h)
    log_liq  = math.log(1 + liquidity)
    days_enc = math.log(1 + (days_left or 180.0))

    # Feature de distancia desde 0.5 (mide cuán extremo es el precio)
    dist_from_half = abs(market_price - 0.5)

    # Interacción: precio extremo + mercado crypto → más incertidumbre
    crypto_extreme = (1 if category == "crypto" else 0) * dist_from_half

    return [
        market_price,        # precio bruto
        dist_from_half,      # distancia de 0.5
        cat_id,              # categoría (0-4)
        log_vol,             # log volumen
        log_liq,             # log liquidez
        spread,              # spread bid-ask
        days_enc,            # log días restantes
        orderflow,           # señal de orderflow [0,1]
        momentum,            # señal de momentum [0,1]
        crypto_extreme,      # interacción crypto × extremismo
        market_price ** 2,   # cuadrática del precio
        (1 - market_price) ** 2,
    ]


FEATURE_NAMES = [
    "price", "dist_from_half", "category", "log_volume", "log_liquidity",
    "spread", "log_days", "orderflow", "momentum", "crypto_extreme",
    "price_sq", "inv_price_sq",
]


class PolymarketMLPredictor:
    """
    Calibrador ML entrenado en resoluciones históricas de Polymarket.
    Predice P(YES | features) para calibrar el precio de mercado.
    """

    def __init__(self):
        self._model  = None
        self._scaler = None
        self._trained_at = 0.0
        self._n_samples  = 0
        self._is_ready   = False
        Path("data").mkdir(exist_ok=True)

    def load_or_train(self) -> bool:
        """
        Carga modelo guardado o entrena desde cero.
        Retorna True si el modelo está listo.
        """
        # Intenta cargar modelo existente
        if MODEL_FILE.exists() and SCALER_FILE.exists():
            age = time.time() - MODEL_FILE.stat().st_mtime
            if age < MODEL_TTL:
                try:
                    import pickle
                    self._model  = pickle.loads(MODEL_FILE.read_bytes())
                    self._scaler = pickle.loads(SCALER_FILE.read_bytes())
                    self._is_ready = True
                    logger.info(f"Modelo ML cargado desde {MODEL_FILE} "
                                f"(edad: {age/3600:.1f}h)")
                    return True
                except Exception as e:
                    logger.warning(f"Error cargando modelo ML: {e}")

        # Entrena nuevo modelo
        return self.train()

    def train(self) -> bool:
        """Descarga datos históricos y entrena el modelo."""
        logger.info("Entrenando modelo ML con datos históricos de Polymarket...")
        try:
            X, y = self._build_training_data()
            if len(X) < MIN_SAMPLES:
                logger.warning(f"Datos insuficientes para ML: {len(X)} < {MIN_SAMPLES}")
                return False
            return self._fit(X, y)
        except Exception as e:
            logger.error(f"Error entrenando ML: {e}")
            return False

    def _build_training_data(self) -> tuple:
        """
        Construye (X, y) desde mercados resueltos de Polymarket.

        Para cada mercado resuelto:
          X = features del mercado (precio, volumen, categoría, etc.)
          y = 1 si resolvió YES, 0 si resolvió NO
        """
        from signals.base_rates import GAMMA_API
        from signals.category_calibration import categorize_market
        import requests

        # Usa caché si disponible y reciente (< 24h)
        if CACHE_FILE.exists():
            try:
                cached = json.loads(CACHE_FILE.read_text())
                if time.time() - cached.get("ts", 0) < 86400:
                    logger.info(f"Usando datos de entrenamiento en caché ({cached.get('n')} muestras)")
                    return (np.array(cached["X"], dtype=np.float32),
                            np.array(cached["y"], dtype=np.float32))
            except Exception:
                pass

        session = requests.Session()
        session.headers["User-Agent"] = "PolymarketBot/2.0"

        markets = []
        for page in range(20):   # hasta 2000 mercados resueltos
            try:
                r = session.get(f"{GAMMA_API}/markets", params={
                    "limit":  100,
                    "offset": page * 100,
                    "closed": "true",
                    "active": "false",
                    "order":  "volume",
                    "ascending": "false",
                }, timeout=12)
                r.raise_for_status()
                batch = r.json()
                if isinstance(batch, dict):
                    batch = batch.get("data", [])
                if not batch:
                    break
                markets.extend(batch)
                if len(batch) < 100:
                    break
                time.sleep(0.3)   # rate limit
            except Exception as e:
                logger.debug(f"Error página {page}: {e}")
                break

        logger.info(f"Mercados resueltos descargados: {len(markets)}")

        X, y = [], []
        for m in markets:
            try:
                # Precio de mercado antes de resolución
                prices = m.get("outcomePrices", [])
                if isinstance(prices, str):
                    prices = json.loads(prices)
                if not prices:
                    continue
                yes_price = float(prices[0])
                if not (0.02 <= yes_price <= 0.98):
                    continue

                # Label: resolvió YES?
                resolution = m.get("resolutionIndex")
                if resolution is None:
                    outcomes = m.get("outcomes", [])
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)
                    winner = m.get("winner") or m.get("question_winner")
                    if outcomes and winner and winner in outcomes:
                        resolution = outcomes.index(winner)
                if resolution is None:
                    continue

                yes_won = int(resolution) == 0

                # Features
                question  = m.get("question", "")
                category  = categorize_market(question)
                volume    = float(m.get("volume24hr") or m.get("volume", 0) or 0)
                liquidity = float(m.get("liquidity") or m.get("liquidityClob") or 0)
                spread    = float(m.get("spread") or 0.05)

                # Estima días activo (proxy de tiempo en mercado)
                from datetime import datetime, timezone
                end_date   = m.get("endDate") or m.get("endDateIso")
                start_date = m.get("startDate") or m.get("createdAt")
                days_active = 30.0  # default
                if end_date and start_date:
                    try:
                        end   = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                        start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                        days_active = max(1.0, (end - start).total_seconds() / 86400)
                    except Exception:
                        pass

                features = _extract_features(
                    market_price=yes_price,
                    category=category,
                    volume_24h=volume,
                    liquidity=liquidity,
                    spread=spread,
                    days_left=days_active,
                )

                X.append(features)
                y.append(1.0 if yes_won else 0.0)

            except Exception:
                continue

        if X:
            # Guarda en caché
            try:
                CACHE_FILE.write_text(json.dumps({
                    "X":  [list(row) for row in X],
                    "y":  y,
                    "n":  len(X),
                    "ts": time.time(),
                }))
            except Exception:
                pass

        logger.info(f"Muestras de entrenamiento generadas: {len(X)}")
        return np.array(X, dtype=np.float32), np.array(y, dtype=np.float32)

    def _fit(self, X: np.ndarray, y: np.ndarray) -> bool:
        """Entrena XGBoost + StandardScaler y guarda el modelo."""
        try:
            from sklearn.preprocessing import StandardScaler
            from sklearn.model_selection import train_test_split
            from xgboost import XGBClassifier

            # Escala features
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)

            # Split train/validation
            X_tr, X_val, y_tr, y_val = train_test_split(
                X_scaled, y, test_size=0.2, random_state=42, stratify=(y > 0.5).astype(int)
            )

            # XGBoost calibrado para probabilidades
            model = XGBClassifier(
                n_estimators    = 150,
                max_depth       = 4,
                learning_rate   = 0.05,
                subsample       = 0.8,
                colsample_bytree= 0.8,
                min_child_weight= 5,
                reg_alpha       = 0.1,
                reg_lambda      = 1.0,
                eval_metric     = "logloss",
                random_state    = 42,
                verbosity       = 0,
            )
            model.fit(
                X_tr, y_tr,
                eval_set        = [(X_val, y_val)],
                verbose         = False,
            )

            # Calibración isotónica para probabilidades bien calibradas
            try:
                from sklearn.calibration import CalibratedClassifierCV
                calibrated = CalibratedClassifierCV(model, method="isotonic", cv="prefit")
                calibrated.fit(X_val, y_val)
                self._model = calibrated
            except Exception:
                self._model = model

            self._scaler     = scaler
            self._trained_at = time.time()
            self._n_samples  = len(X)
            self._is_ready   = True

            # Evaluación en validación
            try:
                from sklearn.metrics import log_loss, brier_score_loss
                probs = self._model.predict_proba(X_val)[:, 1]
                ll   = log_loss(y_val, probs)
                bs   = brier_score_loss(y_val, probs)
                logger.info(
                    f"Modelo ML entrenado: {len(X)} muestras | "
                    f"LogLoss={ll:.4f} | Brier={bs:.4f}"
                )
            except Exception:
                logger.info(f"Modelo ML entrenado: {len(X)} muestras")

            # Guarda el modelo
            import pickle
            MODEL_FILE.write_bytes(pickle.dumps(self._model))
            SCALER_FILE.write_bytes(pickle.dumps(self._scaler))
            return True

        except Exception as e:
            logger.error(f"Error entrenando modelo: {e}")
            return False

    def predict(self, market_price: float, category: str,
                volume_24h: float, liquidity: float,
                spread: float, days_left: Optional[float],
                orderflow: float = 0.5,
                momentum: float = 0.5) -> Optional[float]:
        """
        Predice P(YES) calibrada con el modelo ML.
        Retorna None si el modelo no está disponible.
        """
        if not self._is_ready or self._model is None:
            return None
        try:
            features = _extract_features(
                market_price, category, volume_24h, liquidity,
                spread, days_left, orderflow, momentum
            )
            X = np.array([features], dtype=np.float32)
            X_scaled = self._scaler.transform(X)
            prob = float(self._model.predict_proba(X_scaled)[0][1])
            return round(max(0.01, min(0.99, prob)), 4)
        except Exception as e:
            logger.debug(f"ML predict error: {e}")
            return None

    @property
    def is_ready(self) -> bool:
        return self._is_ready

    @property
    def n_samples(self) -> int:
        return self._n_samples


# Instancia global: se carga/entrena al importar el módulo en background
_ml_predictor: Optional[PolymarketMLPredictor] = None
_ml_lock = __import__("threading").Lock()


def get_ml_predictor() -> PolymarketMLPredictor:
    """Retorna la instancia global del predictor ML (lazy init)."""
    global _ml_predictor
    if _ml_predictor is None:
        with _ml_lock:
            if _ml_predictor is None:
                _ml_predictor = PolymarketMLPredictor()
    return _ml_predictor


def ml_predict(market_price: float, category: str,
               volume_24h: float, liquidity: float,
               spread: float, days_left: Optional[float],
               orderflow: float = 0.5, momentum: float = 0.5) -> Optional[float]:
    """Función conveniente para predecir sin instanciar manualmente."""
    return get_ml_predictor().predict(
        market_price, category, volume_24h, liquidity,
        spread, days_left, orderflow, momentum
    )
