"""
Motor principal de predicción para mercados de Polymarket.

Orquesta el pipeline completo:
  1. Obtiene datos del mercado (orderbook, trades, metadatos)
  2. Calcula señales (orderflow, momentum)
  3. Actualiza modelo bayesiano
  4. Calibra probabilidades
  5. Genera predicción ensemble
  6. Calcula tamaño de posición con Kelly
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

from api.client import PolymarketClient
from signals.orderflow import orderflow_score
from signals.momentum import momentum_score
from models.bayesian import BayesianUpdater
from models.calibration import calibrate_probability, liquidity_adjustment
from models.ensemble import EnsembleModel, SignalBundle, Prediction
from risk.kelly import position_size, expected_value, PositionSize
from config import MIN_LIQUIDITY_USD, MIN_VOLUME_24H, MAX_SPREAD_PCT, EDGE_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class MarketAnalysis:
    """Análisis completo de un mercado."""
    condition_id:  str
    question:      str
    prediction:    Prediction
    position:      PositionSize
    ev:            dict
    bayesian:      dict
    is_opportunity: bool


class PolymarketPredictor:
    """Predictor principal. Analiza mercados y genera recomendaciones."""

    def __init__(self, bankroll: float = 1000.0):
        self.client   = PolymarketClient()
        self.ensemble = EnsembleModel()
        self.bankroll = bankroll
        self._exposure = 0.0

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def scan_markets(self, limit: int = 50,
                     market_type: str = "default") -> list[MarketAnalysis]:
        """
        Escanea los mercados activos y devuelve oportunidades ordenadas por edge.
        """
        markets = self.client.get_markets(limit=limit)
        if not isinstance(markets, list):
            markets = markets.get("data", []) or markets.get("markets", [])

        results = []
        for mkt in markets:
            try:
                analysis = self._analyze_market(mkt, market_type)
                if analysis:
                    results.append(analysis)
            except Exception as e:
                logger.warning(f"Error analizando {mkt.get('conditionId', '?')}: {e}")

        # Ordena por |edge| * confidence
        results.sort(
            key=lambda a: abs(a.prediction.edge) * a.prediction.confidence,
            reverse=True,
        )
        return results

    def analyze_single(self, condition_id: str,
                       market_type: str = "default") -> Optional[MarketAnalysis]:
        """Analiza un mercado específico por condition_id."""
        mkt = self.client.get_market(condition_id)
        return self._analyze_market(mkt, market_type)

    def get_opportunities(self, limit: int = 50,
                          market_type: str = "default") -> list[MarketAnalysis]:
        """Devuelve sólo mercados con edge suficiente (> EDGE_THRESHOLD)."""
        all_markets = self.scan_markets(limit=limit, market_type=market_type)
        return [m for m in all_markets if m.is_opportunity]

    # ------------------------------------------------------------------
    # Pipeline interno
    # ------------------------------------------------------------------
    def _analyze_market(self, mkt: dict,
                        market_type: str) -> Optional[MarketAnalysis]:
        condition_id = mkt.get("conditionId") or mkt.get("condition_id", "")
        question     = mkt.get("question", "Sin descripción")
        end_date     = mkt.get("endDate") or mkt.get("end_date_iso")

        tokens = mkt.get("tokens", [mkt]) if "tokens" in mkt else [mkt]
        yes_token = next(
            (t for t in tokens if t.get("outcome", "").upper() == "YES"),
            tokens[0] if tokens else None,
        )
        if not yes_token:
            return None

        token_id = yes_token.get("token_id") or yes_token.get("tokenId", "")
        if not token_id:
            return None

        # ---- Datos de mercado ----
        orderbook = self.client.get_orderbook(token_id)
        trades    = self.client.get_trades(condition_id)
        spread    = self.client.get_spread(token_id) or 0.05
        mid_price = self.client.get_midpoint(token_id)

        if mid_price is None:
            return None

        volume_24h = float(mkt.get("volume24hr", mkt.get("volume24h", 0)) or 0)

        # ---- Filtros de liquidez ----
        if spread > MAX_SPREAD_PCT:
            logger.debug(f"SKIP {condition_id}: spread {spread:.1%} > {MAX_SPREAD_PCT:.1%}")
            return None
        if volume_24h < MIN_VOLUME_24H:
            logger.debug(f"SKIP {condition_id}: vol24h ${volume_24h:.0f} < ${MIN_VOLUME_24H}")
            return None

        # ---- Señales ----
        of_score  = orderflow_score(orderbook, trades) if trades else 0.5
        mom_score = momentum_score(trades, end_date) if trades else 0.5

        # ---- Actualización Bayesiana ----
        updater = BayesianUpdater(prior_price=mid_price, concentration=12.0)
        updater.update_with_multiple({
            "orderflow": (of_score, 1.5),
            "momentum":  (mom_score, 1.0),
        })
        bayes_prob  = updater.probability
        uncertainty = updater.uncertainty

        # ---- Calibración ----
        calibrated_price = calibrate_probability(mid_price, market_type)
        calibrated_price = liquidity_adjustment(calibrated_price, spread, volume_24h)

        # ---- Ensemble ----
        signals = SignalBundle(
            market_price=mid_price,
            orderflow=of_score,
            momentum=mom_score,
            calibrated=calibrated_price,
            bayesian=bayes_prob,
            uncertainty=uncertainty,
            spread=spread,
            volume_24h=volume_24h,
        )
        prediction = self.ensemble.predict(signals)

        # ---- Kelly + EV ----
        pos = position_size(
            prob=prediction.probability,
            market_price=mid_price,
            bankroll=self.bankroll,
            current_exposure=self._exposure,
            confidence=prediction.confidence,
        )
        ev = expected_value(prediction.probability, mid_price, pos.usd_amount)

        is_opportunity = (
            abs(prediction.edge) >= EDGE_THRESHOLD and
            prediction.confidence >= 0.4 and
            pos.usd_amount > 0
        )

        return MarketAnalysis(
            condition_id   = condition_id,
            question       = question,
            prediction     = prediction,
            position       = pos,
            ev             = ev,
            bayesian       = updater.summary(),
            is_opportunity = is_opportunity,
        )


# ------------------------------------------------------------------
# CLI rápida para pruebas
# ------------------------------------------------------------------
def _fmt(analysis: MarketAnalysis) -> str:
    p = analysis.prediction
    pos = analysis.position
    return (
        f"\n{'='*60}\n"
        f"Mercado : {analysis.question[:70]}\n"
        f"ID      : {analysis.condition_id}\n"
        f"Precio  : {p.market_price:.2%}  →  P(YES) estimado: {p.probability:.2%}\n"
        f"Edge    : {p.edge:+.2%}  |  Confianza: {p.confidence:.0%}\n"
        f"IC 95%  : [{p.ci_lower:.2%}, {p.ci_upper:.2%}]\n"
        f"Tamaño  : ${pos.usd_amount:.2f}  ({pos.rationale})\n"
        f"EV      : ${analysis.ev['ev']:.3f}  (ROI: {analysis.ev['roi']:.1%})\n"
        f"Acción  : {p.recommendation}\n"
    )


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Polymarket Predictor")
    parser.add_argument("--bankroll",  type=float, default=1000.0)
    parser.add_argument("--limit",     type=int,   default=30)
    parser.add_argument("--type",      default="default",
                        choices=list({"politics", "sports", "crypto",
                                      "economics", "default"}))
    parser.add_argument("--market-id", default=None,
                        help="Analiza un solo mercado por condition_id")
    args = parser.parse_args()

    predictor = PolymarketPredictor(bankroll=args.bankroll)

    if args.market_id:
        result = predictor.analyze_single(args.market_id, args.type)
        if result:
            print(_fmt(result))
        else:
            print("No se pudo analizar el mercado (liquidez insuficiente o datos no disponibles).")
    else:
        print(f"Escaneando los mejores {args.limit} mercados activos…")
        opportunities = predictor.get_opportunities(
            limit=args.limit, market_type=args.type
        )

        if not opportunities:
            print("No se encontraron oportunidades con edge suficiente.")
        else:
            print(f"\nEncontradas {len(opportunities)} oportunidades:\n")
            for a in opportunities[:10]:
                print(_fmt(a))
