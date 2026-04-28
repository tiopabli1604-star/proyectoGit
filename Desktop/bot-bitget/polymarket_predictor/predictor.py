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
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from api.client import PolymarketClient
from signals.orderflow import orderflow_score, market_freshness
from signals.momentum import momentum_score
from signals.external import get_external_signal
from signals.base_rates import get_calibration_curve, apply_base_rate
from signals.category_calibration import categorize_market, calibrate_by_category
from signals.cross_market import cross_market_analyzer
from models.bayesian import BayesianUpdater
from models.calibration import calibrate_probability, liquidity_adjustment
from models.ensemble import EnsembleModel, SignalBundle, Prediction
from models.ml_predictor import get_ml_predictor
from risk.kelly import position_size, expected_value, PositionSize
from config import MIN_LIQUIDITY_USD, MIN_VOLUME_24H, MAX_SPREAD_PCT, EDGE_THRESHOLD

logger = logging.getLogger(__name__)


def _days_left(end_date_str: Optional[str]) -> Optional[float]:
    if not end_date_str:
        return None
    try:
        end = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
        return max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 86400)
    except Exception:
        return None


def _composite_score(edge: float, confidence: float,
                     days: Optional[float]) -> float:
    """
    Score de ranking compuesto:
      - Edge alto = mejor oportunidad de valor
      - Confianza alta = señal más fiable
      - Menos días = capital se libera antes (urgencia)

    Fórmula: |edge| * confidence / sqrt(days + 1)
    Así un mercado que cierra en 3 días con edge 10% puntúa más
    que uno con el mismo edge pero que cierra en 6 meses.
    """
    if days is None:
        days = 180.0  # penaliza mercados sin fecha
    urgency = 1.0 / math.sqrt(days + 1)
    return abs(edge) * confidence * urgency


@dataclass
class MarketAnalysis:
    """Análisis completo de un mercado."""
    condition_id:   str
    question:       str
    end_date:       Optional[str]
    days_left:      Optional[float]
    prediction:     Prediction
    position:       PositionSize
    ev:             dict
    bayesian:       dict
    is_opportunity: bool
    external:       dict = field(default_factory=dict)   # señales externas
    score:          float = field(default=0.0)

    def __post_init__(self):
        self.score = _composite_score(
            self.prediction.edge,
            self.prediction.confidence,
            self.days_left,
        )


class PolymarketPredictor:
    """Predictor principal. Analiza mercados y genera recomendaciones."""

    def __init__(self, bankroll: float = 1000.0):
        self.client      = PolymarketClient()
        self.ensemble    = EnsembleModel()
        self.bankroll    = bankroll
        self._exposure   = 0.0
        self._base_rates = get_calibration_curve()   # cargado al inicio, caché 24h

        # Inicia entrenamiento ML en background (no bloquea el arranque)
        import threading
        ml = get_ml_predictor()
        t = threading.Thread(target=ml.load_or_train, daemon=True)
        t.start()

    # ------------------------------------------------------------------
    # API pública
    # ------------------------------------------------------------------
    def scan_markets(self, limit: int = 50, market_type: str = "default",
                     keywords: list[str] = None) -> list[MarketAnalysis]:
        """
        Escanea mercados activos. Si se pasan keywords, pagina todos los mercados
        y filtra por esas palabras en la pregunta/descripción.
        """
        if keywords:
            markets = self.client.get_all_markets(keywords=keywords, max_pages=20)
            logger.info(f"Paginación completa: {len(markets)} mercados con keywords {keywords}")
        else:
            markets = self.client.get_markets(limit=limit)

        # Construye índice de correlación cruzada con todos los mercados del escaneo
        try:
            cross_market_analyzer.build_index(markets)
            logger.debug("Índice de correlación cruzada construido")
        except Exception as e:
            logger.debug(f"No se pudo construir índice cross-market: {e}")

        results = []
        for mkt in markets:
            try:
                analysis = self._analyze_market(mkt, market_type)
                if analysis:
                    results.append(analysis)
            except Exception as e:
                logger.warning(f"Error analizando {mkt.get('conditionId', '?')}: {e}")

        # Ordena por score compuesto: edge × confianza × urgencia temporal
        results.sort(key=lambda a: a.score, reverse=True)
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
        end_date     = mkt.get("endDate") or mkt.get("endDateIso") or mkt.get("end_date_iso")

        # --- Token ID: viene en clobTokenIds (lista) ---
        clob_token_ids = mkt.get("clobTokenIds", [])
        if isinstance(clob_token_ids, str):
            import json as _json
            try:
                clob_token_ids = _json.loads(clob_token_ids)
            except Exception:
                clob_token_ids = []
        token_id = clob_token_ids[0] if clob_token_ids else None
        if not token_id:
            return None

        # --- Precio YES: viene en outcomePrices (lista de strings) ---
        outcome_prices = mkt.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            import json as _json
            try:
                outcome_prices = _json.loads(outcome_prices)
            except Exception:
                outcome_prices = []
        if not outcome_prices:
            return None
        try:
            mid_price = float(outcome_prices[0])
        except (ValueError, IndexError):
            return None

        if not (0.01 <= mid_price <= 0.99):
            return None

        # --- Descarta mercados ya terminados ---
        dl = _days_left(end_date)
        if dl is not None and dl <= 0:
            logger.debug(f"SKIP {condition_id}: mercado ya expirado")
            return None

        # --- Spread y volumen ya vienen en el mercado ---
        spread     = float(mkt.get("spread", 0.05) or 0.05)
        volume_24h = float(mkt.get("volume24hr") or mkt.get("volume24hrClob") or 0)
        liquidity  = float(mkt.get("liquidity") or mkt.get("liquidityClob") or 0)

        # ---- Datos CLOB (orderbook y trades) ----
        try:
            orderbook = self.client.get_orderbook(token_id)
        except Exception:
            orderbook = {"bids": [], "asks": []}
        try:
            trades = self.client.get_trades(condition_id)
            if not isinstance(trades, list):
                trades = []
        except Exception:
            trades = []

        # ---- Filtros de liquidez ----
        if spread > MAX_SPREAD_PCT:
            logger.debug(f"SKIP {condition_id}: spread {spread:.1%} > {MAX_SPREAD_PCT:.1%}")
            return None
        if liquidity < MIN_LIQUIDITY_USD and volume_24h < MIN_VOLUME_24H:
            logger.debug(f"SKIP {condition_id}: liquidez ${liquidity:.0f}, vol24h ${volume_24h:.0f}")
            return None

        # ---- Categoría del mercado ----
        category = categorize_market(question)

        # ---- Señales internas ----
        of_score   = orderflow_score(orderbook, trades) if trades else 0.5
        mom_score  = momentum_score(trades, end_date) if trades else 0.5
        freshness  = market_freshness(trades)   # 0 = dormido, 1 = activo

        # ---- Señales externas (Manifold, Kalshi, noticias NLP) ----
        topic_kw = [w for w in question.lower().split()
                    if len(w) > 3 and w not in {
                        "will", "does", "have", "been", "that", "with",
                        "this", "from", "they", "what", "when", "which",
                        "2024", "2025", "2026", "2027"}][:6]
        ext = get_external_signal(question, topic_keywords=topic_kw)
        external_prob    = ext.get("external_prob")
        external_weight  = ext.get("source_weight", 0.0)
        news_score_raw   = ext.get("news", {}).get("score", 0.0)
        news_confidence  = ext.get("news", {}).get("confidence", 0.3)
        # Convierte news score [-1,1] a [0,1]
        news_signal = (news_score_raw + 1) / 2

        # ---- Correlación cruzada entre mercados ----
        cross_signal = {}
        try:
            cross_signal = cross_market_analyzer.get_signal(
                question=question,
                market_price=mid_price,
                condition_id=condition_id,
            )
        except Exception:
            pass
        cross_prob       = cross_signal.get("correlated_prob")
        cross_confidence = cross_signal.get("confidence", 0.0)
        cross_contradiction = cross_signal.get("contradiction", False)

        # ---- Predicción ML ----
        ml_prob = None
        try:
            ml = get_ml_predictor()
            if ml.is_ready:
                ml_prob = ml.predict(
                    market_price=mid_price,
                    category=category,
                    volume_24h=volume_24h,
                    liquidity=liquidity,
                    spread=spread,
                    days_left=dl,
                    orderflow=of_score,
                    momentum=mom_score,
                )
        except Exception:
            pass

        # ---- Actualización Bayesiana ----
        updater = BayesianUpdater(prior_price=mid_price, concentration=10.0)

        signals_dict = {
            "orderflow": (of_score,  1.0),
            "momentum":  (mom_score, 0.8),
        }
        # Noticias: peso según confianza del análisis NLP
        if abs(news_score_raw) > 0.05 and news_confidence > 0.2:
            news_strength = 0.4 + news_confidence * 1.2   # 0.4 – 1.48
            signals_dict["news"] = (news_signal, news_strength)

        # Señal externa (Manifold/Kalshi): peso por similitud
        if external_prob is not None and external_weight > 0.2:
            ext_strength = external_weight * 4.0
            signals_dict["external"] = (external_prob, ext_strength)

        # Correlación cruzada: señal independiente de mercados relacionados
        if cross_prob is not None and cross_confidence > 0.3 and not cross_contradiction:
            cross_strength = cross_confidence * 2.5
            signals_dict["cross_market"] = (cross_prob, cross_strength)

        # Predicción ML: señal adicional si está disponible
        if ml_prob is not None:
            signals_dict["ml"] = (ml_prob, 1.5)

        updater.update_with_multiple(signals_dict)
        bayes_prob  = updater.probability
        uncertainty = updater.uncertainty

        # ---- Calibración por categoría ----
        calibrated_price = calibrate_probability(mid_price, market_type)
        calibrated_price = liquidity_adjustment(calibrated_price, spread, volume_24h)
        calibrated_price = apply_base_rate(calibrated_price, self._base_rates)
        # Calibración específica a la categoría del mercado
        calibrated_price, cat_confidence = calibrate_by_category(
            calibrated_price, category, dl
        )

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

        # Aplica ajuste de confianza de la categoría
        adjusted_confidence = min(0.95, prediction.confidence * cat_confidence)

        # Mercados dormidos con edge: ligero boost de confianza
        if freshness < 0.3 and abs(prediction.edge) > 0.05:
            adjusted_confidence = min(0.95, adjusted_confidence * 1.1)

        # Si hay contradicción en cross-market: penaliza confianza
        if cross_contradiction:
            adjusted_confidence = adjusted_confidence * 0.75

        if adjusted_confidence != prediction.confidence:
            prediction = Prediction(
                probability  = prediction.probability,
                market_price = prediction.market_price,
                edge         = prediction.edge,
                ci_lower     = prediction.ci_lower,
                ci_upper     = prediction.ci_upper,
                confidence   = adjusted_confidence,
                signals      = prediction.signals,
            )

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

        # Enriquece el dict de señales externas con cross-market y ML
        ext_enriched = {
            **ext,
            "category":          category,
            "cross_market":      cross_signal if cross_signal else {},
            "ml_prob":           ml_prob,
            "cat_confidence":    cat_confidence,
        }

        return MarketAnalysis(
            condition_id   = condition_id,
            question       = question,
            end_date       = end_date,
            days_left      = dl,
            prediction     = prediction,
            position       = pos,
            ev             = ev,
            bayesian       = updater.summary(),
            is_opportunity = is_opportunity,
            external       = ext_enriched,
        )


# ------------------------------------------------------------------
# CLI rápida para pruebas
# ------------------------------------------------------------------
def _fmt(analysis: MarketAnalysis, rank: int = 0) -> str:
    p   = analysis.prediction
    pos = analysis.position

    if analysis.days_left is not None:
        d = analysis.days_left
        if d < 1:
            tiempo = f"{d*24:.0f}h"
        elif d < 7:
            tiempo = f"{d:.1f} días"
        elif d < 60:
            tiempo = f"{d/7:.1f} semanas"
        else:
            tiempo = f"{d/30:.1f} meses"
        cierre = f"{analysis.end_date[:10] if analysis.end_date else '?'}  ({tiempo} restantes)"
    else:
        cierre = "Sin fecha"

    rank_str = f"#{rank}  " if rank else ""
    return (
        f"\n{'='*60}\n"
        f"{rank_str}Score   : {analysis.score:.4f}\n"
        f"Mercado : {analysis.question[:70]}\n"
        f"Cierre  : {cierre}\n"
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

    # Topics predefinidos → keywords que se buscan en la pregunta del mercado
    TOPICS = {
        "elon":       ["elon", "musk", "tesla", "spacex", "doge", "x.com",
                       "dogecoin", "department of government", "doge cut",
                       "twitter", "grok", "neuralink", "starlink"],
        "geopolitica":["ukraine", "russia", "ceasefire", "war", "nato", "china",
                       "taiwan", "iran", "israel", "gaza", "trump", "sanctions"],
        "crypto":     ["bitcoin", "ethereum", "btc", "eth", "crypto", "solana",
                       "xrp", "coinbase", "binance"],
        "deportes":   ["nba", "nfl", "nhl", "stanley cup", "super bowl",
                       "championship", "world cup", "wimbledon"],
    }

    parser = argparse.ArgumentParser(description="Polymarket Predictor")
    parser.add_argument("--bankroll",  type=float, default=1000.0)
    parser.add_argument("--limit",     type=int,   default=100)
    parser.add_argument("--type",      default="default",
                        choices=["politics", "sports", "crypto", "economics", "default"])
    parser.add_argument("--topic",     default=None,
                        choices=list(TOPICS.keys()),
                        help="Filtra por tema: elon, geopolitica, crypto, deportes")
    parser.add_argument("--keywords",  default=None,
                        help="Palabras clave separadas por coma (ej: trump,tariff)")
    parser.add_argument("--market-id", default=None,
                        help="Analiza un solo mercado por condition_id")
    parser.add_argument("--show-all",  action="store_true",
                        help="Muestra todos los mercados aunque no haya edge suficiente")
    parser.add_argument("--min-edge",  type=float, default=None,
                        help="Override del edge mínimo (ej: 0.01 para 1%%)")
    args = parser.parse_args()

    # Construye lista de keywords
    kw = None
    if args.topic:
        kw = TOPICS[args.topic]
    elif args.keywords:
        kw = [k.strip() for k in args.keywords.split(",")]

    predictor = PolymarketPredictor(bankroll=args.bankroll)

    if args.market_id:
        result = predictor.analyze_single(args.market_id, args.type)
        if result:
            print(_fmt(result))
        else:
            print("No se pudo analizar el mercado (liquidez insuficiente o datos no disponibles).")
    else:
        if kw:
            print(f"Buscando en TODOS los mercados con keywords: {kw}…")
        else:
            print(f"Escaneando los {args.limit} mercados más activos…")
        all_results = predictor.scan_markets(limit=args.limit, market_type=args.type,
                                             keywords=kw)

        min_edge = args.min_edge if args.min_edge is not None else EDGE_THRESHOLD
        opportunities = [m for m in all_results if abs(m.prediction.edge) >= min_edge
                         and m.prediction.confidence >= 0.4] if not args.show_all else all_results

        if not opportunities and not args.show_all:
            print(f"No se encontraron oportunidades con edge >= {min_edge:.0%}.")
            print(f"Se analizaron {len(all_results)} mercados. Usa --show-all para verlos todos.")
            if all_results:
                print(f"\nMejor mercado encontrado (edge insuficiente):")
                print(_fmt(all_results[0], rank=1))
        else:
            top = opportunities[:10]
            print(f"\nTop {len(top)} oportunidades (ordenadas por edge × confianza × urgencia):\n")
            for i, a in enumerate(top, 1):
                print(_fmt(a, rank=i))
