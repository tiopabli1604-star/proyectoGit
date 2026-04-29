"""
Motor de backtesting para PolyiClaude.

Simula cómo habría funcionado el algoritmo en mercados ya resueltos.
Mide: precisión de predicción, P&L simulado, Sharpe ratio, Brier score.

Proceso:
  1. Descarga N mercados resueltos de Polymarket
  2. Para cada mercado resuelto, ejecuta nuestro algoritmo de predicción
     usando los datos disponibles ANTES de la resolución
  3. Compara la predicción con el resultado real
  4. Calcula métricas de performance

Métricas calculadas:
  - Accuracy:     % veces que el algoritmo acertó la dirección
  - Brier Score:  calidad de las probabilidades (0=perfecto, 1=pésimo)
  - Log Loss:     otra métrica de calibración
  - P&L simulado: si hubieras apostado Kelly en cada señal
  - Sharpe Ratio: P&L ajustado por volatilidad
  - Calibration:  curva predicted vs actual (¿al 70% pasa 70% de las veces?)
"""

import json
import logging
import time
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

GAMMA_API   = "https://gamma-api.polymarket.com"
RESULTS_FILE = Path("data/backtest_results.json")


@dataclass
class BacktestTrade:
    """Resultado de una apuesta simulada."""
    condition_id:  str
    question:      str
    category:      str
    market_price:  float
    predicted_prob: float
    edge:          float
    confidence:    float
    resolved_yes:  bool          # resultado real
    bet_amount:    float         # tamaño Kelly simulado
    pnl:           float         # ganancia/pérdida simulada
    correct:       bool          # ¿acertó dirección?
    days_to_close: Optional[float]


@dataclass
class BacktestResults:
    """Métricas completas del backtest."""
    n_markets:       int = 0
    n_traded:        int = 0       # con edge >= umbral
    accuracy:        float = 0.0
    brier_score:     float = 0.0
    log_loss:        float = 0.0
    total_pnl:       float = 0.0
    roi:             float = 0.0
    sharpe_ratio:    float = 0.0
    max_drawdown:    float = 0.0
    win_rate:        float = 0.0
    avg_edge:        float = 0.0
    avg_confidence:  float = 0.0
    calibration:     dict = field(default_factory=dict)
    by_category:     dict = field(default_factory=dict)
    trades:          list = field(default_factory=list)
    generated_at:    str  = ""


class BacktestEngine:
    """Motor de backtesting sobre mercados resueltos de Polymarket."""

    def __init__(self, bankroll: float = 1000.0,
                 min_edge: float = 0.04,
                 min_confidence: float = 0.40,
                 kelly_fraction: float = 0.25):
        self.bankroll        = bankroll
        self.min_edge        = min_edge
        self.min_confidence  = min_confidence
        self.kelly_fraction  = kelly_fraction

    def run(self, n_markets: int = 300,
            use_cache: bool = True) -> BacktestResults:
        """
        Ejecuta el backtest completo.

        n_markets: cuántos mercados resueltos usar (más = más lento pero mejor)
        use_cache: reutiliza datos descargados si tienen < 24h
        """
        logger.info(f"Iniciando backtest sobre {n_markets} mercados resueltos...")
        t0 = time.time()

        markets = self._load_resolved_markets(n_markets, use_cache)
        logger.info(f"Mercados resueltos cargados: {len(markets)}")

        if not markets:
            logger.error("No se pudieron obtener mercados resueltos")
            return BacktestResults()

        trades = self._simulate_trades(markets)
        results = self._calculate_metrics(trades, len(markets))
        results.generated_at = datetime.now(timezone.utc).isoformat()

        elapsed = time.time() - t0
        logger.info(
            f"Backtest completado en {elapsed:.1f}s — "
            f"{results.n_traded} trades | "
            f"Accuracy: {results.accuracy:.1%} | "
            f"P&L: ${results.total_pnl:.2f} | "
            f"Sharpe: {results.sharpe_ratio:.2f}"
        )

        # Guarda resultados
        self._save_results(results)
        return results

    def _load_resolved_markets(self, n: int, use_cache: bool) -> list[dict]:
        """Carga mercados resueltos, con caché de 24h."""
        cache_file = Path("data/backtest_markets_cache.json")
        if use_cache and cache_file.exists():
            try:
                cached = json.loads(cache_file.read_text())
                if time.time() - cached.get("ts", 0) < 86400:
                    logger.info("Usando caché de mercados resueltos")
                    return cached["markets"][:n]
            except Exception:
                pass

        import requests
        session = requests.Session()
        session.headers["User-Agent"] = "PolymarketBot/2.0"

        markets = []
        pages   = math.ceil(n / 100)
        for page in range(pages):
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
                time.sleep(0.2)
            except Exception as e:
                logger.debug(f"Error cargando página {page}: {e}")
                break

        if markets:
            cache_file.parent.mkdir(exist_ok=True)
            cache_file.write_text(json.dumps({"markets": markets, "ts": time.time()}))

        return markets[:n]

    def _simulate_trades(self, markets: list[dict]) -> list[BacktestTrade]:
        """
        Para cada mercado resuelto, simula lo que habría hecho el algoritmo.
        """
        from signals.category_calibration import categorize_market, calibrate_by_category
        from signals.base_rates import get_calibration_curve, apply_base_rate
        from models.ml_predictor import get_ml_predictor
        from models.calibration import calibrate_probability, liquidity_adjustment
        from models.bayesian import BayesianUpdater

        base_rates = get_calibration_curve()
        ml = get_ml_predictor()

        trades = []

        for m in markets:
            try:
                trade = self._simulate_single(m, base_rates, ml,
                                              calibrate_by_category,
                                              apply_base_rate,
                                              calibrate_probability,
                                              liquidity_adjustment,
                                              BayesianUpdater)
                if trade is not None:
                    trades.append(trade)
            except Exception as e:
                logger.debug(f"Error simulando mercado: {e}")

        return trades

    def _simulate_single(self, m: dict, base_rates: dict, ml,
                          calibrate_by_category, apply_base_rate,
                          calibrate_probability, liquidity_adjustment,
                          BayesianUpdater) -> Optional[BacktestTrade]:
        """Simula una sola apuesta en un mercado resuelto."""
        # Precio de mercado antes de resolución
        prices = m.get("outcomePrices", [])
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except Exception:
                return None
        if not prices:
            return None
        try:
            yes_price = float(prices[0])
        except (ValueError, IndexError):
            return None
        if not (0.02 <= yes_price <= 0.98):
            return None

        # Resultado real
        resolution = m.get("resolutionIndex")
        if resolution is None:
            outcomes = m.get("outcomes", [])
            if isinstance(outcomes, str):
                try:
                    outcomes = json.loads(outcomes)
                except Exception:
                    outcomes = []
            winner = m.get("winner") or m.get("question_winner")
            if outcomes and winner and winner in outcomes:
                try:
                    resolution = outcomes.index(winner)
                except Exception:
                    return None
        if resolution is None:
            return None
        resolved_yes = int(resolution) == 0

        question = m.get("question", "")
        category = categorize_market(question)
        volume   = float(m.get("volume24hr") or m.get("volume", 0) or 0)
        liquidity = float(m.get("liquidity") or 0)
        spread   = float(m.get("spread") or 0.05)

        # Simula el pipeline del predictor
        calibrated = calibrate_probability(yes_price, "default")
        calibrated = liquidity_adjustment(calibrated, spread, volume)
        calibrated = apply_base_rate(calibrated, base_rates)
        calibrated, cat_conf = calibrate_by_category(calibrated, category)

        updater = BayesianUpdater(prior_price=yes_price, concentration=10.0)
        updater.update_with_multiple({
            "calibrated": (calibrated, 1.5),
        })

        if ml.is_ready:
            ml_prob = ml.predict(yes_price, category, volume, liquidity, spread, 30.0)
            if ml_prob is not None:
                updater.update_with_multiple({"ml": (ml_prob, 1.5)})

        predicted_prob = updater.probability
        edge = predicted_prob - yes_price
        confidence = min(0.9, updater.uncertainty * cat_conf * 0.8 + 0.4)

        # Solo simula si hay edge suficiente
        if abs(edge) < self.min_edge or confidence < self.min_confidence:
            return None

        # Tamaño de posición Kelly
        if edge > 0:
            # Apostar YES: precio es yes_price, ganamos 1/yes_price - 1
            odds = 1.0 / yes_price - 1.0
            kelly = (predicted_prob * (1 + odds) - 1) / odds if odds > 0 else 0
        else:
            # Apostar NO
            no_price = 1 - yes_price
            odds = 1.0 / no_price - 1.0
            kelly = ((1 - predicted_prob) * (1 + odds) - 1) / odds if odds > 0 else 0

        kelly = max(0, min(kelly * self.kelly_fraction, 0.10))  # máx 10% bankroll
        bet_amount = self.bankroll * kelly

        if bet_amount < 1.0:
            return None

        # Calcula P&L real
        if edge > 0:   # apostó YES
            pnl = bet_amount * (1.0 / yes_price - 1.0) if resolved_yes else -bet_amount
            correct = resolved_yes
        else:          # apostó NO
            pnl = bet_amount * (1.0 / (1 - yes_price) - 1.0) if not resolved_yes else -bet_amount
            correct = not resolved_yes

        # Días hasta resolución (estimado)
        days_to_close = None
        end_date   = m.get("endDate") or m.get("endDateIso")
        start_date = m.get("startDate") or m.get("createdAt")
        if end_date and start_date:
            try:
                end   = datetime.fromisoformat(end_date.replace("Z", "+00:00"))
                start = datetime.fromisoformat(start_date.replace("Z", "+00:00"))
                days_to_close = (end - start).total_seconds() / 86400
            except Exception:
                pass

        return BacktestTrade(
            condition_id   = m.get("conditionId", ""),
            question       = question[:80],
            category       = category,
            market_price   = round(yes_price, 4),
            predicted_prob = round(predicted_prob, 4),
            edge           = round(edge, 4),
            confidence     = round(confidence, 4),
            resolved_yes   = resolved_yes,
            bet_amount     = round(bet_amount, 2),
            pnl            = round(pnl, 2),
            correct        = correct,
            days_to_close  = days_to_close,
        )

    def _calculate_metrics(self, trades: list[BacktestTrade],
                            n_total: int) -> BacktestResults:
        """Calcula todas las métricas de performance."""
        if not trades:
            return BacktestResults(n_markets=n_total)

        n = len(trades)
        correct  = [t for t in trades if t.correct]
        accuracy = len(correct) / n

        # Brier Score: E[(p - y)^2]
        brier = np.mean([
            (t.predicted_prob - (1.0 if t.resolved_yes else 0.0)) ** 2
            for t in trades
        ])

        # Log Loss
        eps = 1e-7
        log_loss = -np.mean([
            math.log(max(t.predicted_prob, eps)) if t.resolved_yes
            else math.log(max(1 - t.predicted_prob, eps))
            for t in trades
        ])

        # P&L y Sharpe
        pnls = [t.pnl for t in trades]
        total_pnl = sum(pnls)
        invested  = sum(t.bet_amount for t in trades)
        roi       = total_pnl / invested if invested > 0 else 0.0
        pnl_std   = float(np.std(pnls)) if len(pnls) > 1 else 1.0
        sharpe    = (float(np.mean(pnls)) / pnl_std * math.sqrt(252 / max(n, 1))
                     if pnl_std > 0 else 0.0)

        # Max Drawdown
        cumulative = np.cumsum(pnls)
        running_max = np.maximum.accumulate(cumulative)
        drawdowns = cumulative - running_max
        max_drawdown = float(np.min(drawdowns)) if len(drawdowns) > 0 else 0.0

        # Win rate (entre trades ejecutados)
        wins = [t for t in trades if t.pnl > 0]
        win_rate = len(wins) / n

        # Calibración: precio de mercado vs tasa de resolución real
        calibration = {}
        for bucket in [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]:
            in_bucket = [t for t in trades
                         if abs(t.market_price - bucket) < 0.05]
            if len(in_bucket) >= 3:
                actual_rate = sum(1 for t in in_bucket if t.resolved_yes) / len(in_bucket)
                calibration[str(bucket)] = {
                    "predicted": bucket,
                    "actual":    round(actual_rate, 3),
                    "n":         len(in_bucket),
                    "error":     round(actual_rate - bucket, 3),
                }

        # Por categoría
        categories = set(t.category for t in trades)
        by_category = {}
        for cat in categories:
            cat_trades = [t for t in trades if t.category == cat]
            cat_wins   = [t for t in cat_trades if t.pnl > 0]
            by_category[cat] = {
                "n":        len(cat_trades),
                "win_rate": round(len(cat_wins) / len(cat_trades), 3) if cat_trades else 0,
                "total_pnl": round(sum(t.pnl for t in cat_trades), 2),
                "avg_edge": round(float(np.mean([abs(t.edge) for t in cat_trades])), 4),
            }

        # Serializa trades para guardado
        trades_serialized = [
            {
                "question":      t.question,
                "category":      t.category,
                "market_price":  t.market_price,
                "predicted":     t.predicted_prob,
                "edge":          t.edge,
                "resolved_yes":  t.resolved_yes,
                "bet":           t.bet_amount,
                "pnl":           t.pnl,
                "correct":       t.correct,
            }
            for t in trades[:50]   # guarda solo los primeros 50
        ]

        return BacktestResults(
            n_markets      = n_total,
            n_traded       = n,
            accuracy       = round(accuracy, 4),
            brier_score    = round(float(brier), 4),
            log_loss       = round(float(log_loss), 4),
            total_pnl      = round(total_pnl, 2),
            roi            = round(roi, 4),
            sharpe_ratio   = round(sharpe, 3),
            max_drawdown   = round(max_drawdown, 2),
            win_rate       = round(win_rate, 4),
            avg_edge       = round(float(np.mean([abs(t.edge) for t in trades])), 4),
            avg_confidence = round(float(np.mean([t.confidence for t in trades])), 4),
            calibration    = calibration,
            by_category    = by_category,
            trades         = trades_serialized,
        )

    def _save_results(self, results: BacktestResults) -> None:
        RESULTS_FILE.parent.mkdir(exist_ok=True)
        data = {
            "n_markets":       results.n_markets,
            "n_traded":        results.n_traded,
            "accuracy":        results.accuracy,
            "brier_score":     results.brier_score,
            "log_loss":        results.log_loss,
            "total_pnl":       results.total_pnl,
            "roi":             results.roi,
            "sharpe_ratio":    results.sharpe_ratio,
            "max_drawdown":    results.max_drawdown,
            "win_rate":        results.win_rate,
            "avg_edge":        results.avg_edge,
            "avg_confidence":  results.avg_confidence,
            "calibration":     results.calibration,
            "by_category":     results.by_category,
            "trades":          results.trades,
            "generated_at":    results.generated_at,
        }
        RESULTS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
        logger.info(f"Resultados guardados en {RESULTS_FILE}")


def run_backtest(n_markets: int = 300, bankroll: float = 1000.0,
                 min_edge: float = 0.04) -> BacktestResults:
    """Función de conveniencia para lanzar un backtest."""
    engine = BacktestEngine(bankroll=bankroll, min_edge=min_edge)
    return engine.run(n_markets=n_markets)


def format_backtest_telegram(results: BacktestResults) -> str:
    """Formatea resultados del backtest para enviar por Telegram."""
    if results.n_traded == 0:
        return "❌ El backtest no encontró suficientes datos."

    emoji_acc  = "✅" if results.accuracy >= 0.55 else ("⚠️" if results.accuracy >= 0.50 else "❌")
    emoji_pnl  = "🟢" if results.total_pnl >= 0 else "🔴"
    emoji_sh   = "🟢" if results.sharpe_ratio >= 0.5 else ("🟡" if results.sharpe_ratio >= 0 else "🔴")

    cat_lines = ""
    for cat, stats in sorted(results.by_category.items(),
                              key=lambda x: x[1]["total_pnl"], reverse=True):
        wr = stats["win_rate"] * 100
        cat_lines += (
            f"\n  • {cat}: {stats['n']} trades | "
            f"Win {wr:.0f}% | P&L ${stats['total_pnl']:+.0f}"
        )

    cal_lines = ""
    for bucket, cal in sorted(results.calibration.items(), key=lambda x: float(x[0])):
        error = cal["error"]
        arrow = "↑" if error > 0.05 else ("↓" if error < -0.05 else "≈")
        cal_lines += f"\n  {float(bucket):.0%}: real {cal['actual']:.0%} {arrow} ({cal['n']} mkts)"

    return (
        f"📊 <b>Backtest — {results.n_markets} mercados resueltos</b>\n"
        f"Fecha: {results.generated_at[:10] if results.generated_at else '?'}\n"
        f"{'━'*38}\n"
        f"\n<b>Performance</b>\n"
        f"{emoji_acc} Accuracy: <b>{results.accuracy:.1%}</b>  "
        f"(de {results.n_traded} trades con edge ≥ {4:.0f}%)\n"
        f"{emoji_pnl} P&L simulado: <b>${results.total_pnl:+.2f}</b>  "
        f"(ROI: {results.roi:+.1%})\n"
        f"{emoji_sh} Sharpe ratio: <b>{results.sharpe_ratio:.2f}</b>\n"
        f"🏆 Win rate: <b>{results.win_rate:.1%}</b>\n"
        f"📉 Max drawdown: <b>${results.max_drawdown:.2f}</b>\n"
        f"\n<b>Calidad de predicciones</b>\n"
        f"Brier Score: <b>{results.brier_score:.4f}</b>  "
        f"(0=perfecto, 0.25=random)\n"
        f"Edge medio: <b>{results.avg_edge:.1%}</b>\n"
        f"\n<b>Por categoría</b>{cat_lines}\n"
        f"\n<b>Calibración del precio</b>{cal_lines}\n"
    )
