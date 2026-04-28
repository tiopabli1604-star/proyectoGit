"""
Correlación cruzada entre mercados relacionados.

El edge real viene de:
1. Detectar mercados correlacionados donde sus precios divergen
   (arbitraje de información entre preguntas relacionadas).
2. Agregar señales de mercados complementarios (si A dice 70% y B dice 65%
   sobre el mismo evento desde ángulos distintos → mayor confianza).
3. Detectar contradicciones lógicas (A gana torneo + B gana torneo → imposible).

Ejemplo de uso:
  analyzer = CrossMarketAnalyzer()
  analyzer.build_index(all_markets)   # una sola vez por escaneo
  signal = analyzer.get_signal("Will Trump win 2024?", 0.65)
  # → {correlated_prob: 0.68, contradiction: False, confidence: 0.7}
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Entidades que definen mercados "del mismo evento"
# Si dos mercados comparten entidad + tipo → pueden ser contradictorios
_COMPETITION_PATTERNS = [
    r"\bwinner\b", r"\bwin the\b", r"\bchampion\b", r"\bfirst place\b",
    r"\bnominee\b", r"\bcandidate\b", r"\bpresident\b", r"\bprime minister\b",
]


class CrossMarketAnalyzer:
    """
    Índice TF-IDF de todos los mercados activos para correlación cruzada.
    Thread-safe. Se reconstruye cada vez que se llama build_index().
    """

    def __init__(self):
        self._lock     = threading.Lock()
        self._markets  = []         # lista de dicts con 'question' y 'price'
        self._matrix   = None       # TF-IDF matrix (sparse)
        self._vectorizer = None
        self._built_at = 0.0

    def build_index(self, markets: list[dict]) -> None:
        """
        Construye el índice TF-IDF con todos los mercados del escaneo.
        Llamar una vez por ciclo de escaneo, antes de analizar mercados.
        """
        if not markets:
            return

        questions = []
        market_data = []
        for m in markets:
            q = m.get("question", "")
            if not q:
                continue
            prices = m.get("outcomePrices", [])
            if isinstance(prices, str):
                import json
                try:
                    prices = json.loads(prices)
                except Exception:
                    prices = []
            try:
                price = float(prices[0]) if prices else None
            except (ValueError, IndexError):
                price = None
            if price is None or not (0.01 <= price <= 0.99):
                continue
            questions.append(q)
            market_data.append({
                "question":     q,
                "price":        price,
                "condition_id": m.get("conditionId") or m.get("condition_id", ""),
                "volume":       float(m.get("volume24hr") or m.get("volume24hrClob") or 0),
                "liquidity":    float(m.get("liquidity") or m.get("liquidityClob") or 0),
            })

        if len(questions) < 3:
            return

        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            import re

            clean_qs = [re.sub(r"[^\w\s]", " ", q.lower()) for q in questions]
            vect = TfidfVectorizer(
                ngram_range=(1, 2),
                max_features=1000,
                min_df=1,
                stop_words=["will", "does", "have", "been", "that", "with",
                            "this", "from", "they", "what", "when", "which",
                            "the", "and", "for", "are", "was", "were"],
            )
            matrix = vect.fit_transform(clean_qs)

            with self._lock:
                self._markets    = market_data
                self._vectorizer = vect
                self._matrix     = matrix
                self._built_at   = time.time()

            logger.debug(f"CrossMarketAnalyzer: índice construido con {len(market_data)} mercados")
        except Exception as e:
            logger.debug(f"CrossMarketAnalyzer build error: {e}")

    def get_signal(self, question: str, market_price: float,
                   condition_id: str = "",
                   min_similarity: float = 0.35,
                   top_k: int = 5) -> dict:
        """
        Encuentra mercados correlacionados y calcula una señal agregada.

        Retorna:
          {
            'correlated_prob':  float | None,  # probabilidad implícita de correlatos
            'contradiction':    bool,          # hay mercado contradictorio
            'confidence':       float,         # confianza en la señal (0-1)
            'n_correlated':     int,           # número de mercados correlacionados
            'correlated':       list[dict],    # detalles de los correlatos
          }
        """
        empty = {
            "correlated_prob": None,
            "contradiction":   False,
            "confidence":      0.0,
            "n_correlated":    0,
            "correlated":      [],
        }

        with self._lock:
            if self._vectorizer is None or self._matrix is None:
                return empty
            vect   = self._vectorizer
            matrix = self._matrix
            mkts   = self._markets

        try:
            import re
            from sklearn.metrics.pairwise import cosine_similarity as cos_sim

            clean_q = re.sub(r"[^\w\s]", " ", question.lower())
            q_vec   = vect.transform([clean_q])
            sims    = cos_sim(q_vec, matrix)[0]

            # Candidatos: similares pero no idénticos
            candidates = []
            for i, sim in enumerate(sims):
                m = mkts[i]
                if m["condition_id"] == condition_id:
                    continue
                if sim >= min_similarity:
                    candidates.append((float(sim), m))

            candidates.sort(key=lambda x: x[0], reverse=True)
            top_candidates = candidates[:top_k]

            if not top_candidates:
                return empty

            # Detecta contradicción: mismo tipo de mercado pero para diferentes
            # ganadores del mismo evento (ej: "A wins" + "B wins" mismo torneo)
            contradiction = _detect_logical_contradiction(question, top_candidates)

            # Agrega probabilidades ponderadas por similitud y volumen
            weighted_probs = []
            correlated = []
            for sim, m in top_candidates:
                # Peso: similitud × log(1 + volume) para dar más peso a mercados líquidos
                import math
                vol_weight = math.log(1 + m["volume"]) if m["volume"] > 0 else 0.1
                weight = sim * vol_weight
                weighted_probs.append((m["price"], weight))
                correlated.append({
                    "question":  m["question"][:60],
                    "price":     m["price"],
                    "similarity": round(sim, 3),
                    "volume":    m["volume"],
                })

            total_weight = sum(w for _, w in weighted_probs)
            if total_weight == 0:
                return empty

            agg_prob = sum(p * w for p, w in weighted_probs) / total_weight

            # La señal de correlación solo ajusta, no reemplaza.
            # Si el precio correlado diverge mucho del mercado → señal más fuerte.
            divergence = abs(agg_prob - market_price)
            # Confianza: cuántos mercados correlados y cuán similares son
            best_sim = top_candidates[0][0]
            n = len(top_candidates)
            confidence = min(0.85, best_sim * (1 - 1 / (n + 1)))

            return {
                "correlated_prob": round(agg_prob, 4),
                "contradiction":   contradiction,
                "confidence":      round(confidence, 3),
                "n_correlated":    n,
                "divergence":      round(divergence, 4),
                "correlated":      correlated,
            }

        except Exception as e:
            logger.debug(f"CrossMarket get_signal error: {e}")
            return empty


def _detect_logical_contradiction(question: str,
                                   candidates: list[tuple]) -> bool:
    """
    Detecta si algún candidato es lógicamente contradictorio con el mercado.
    Heurística: dos mercados sobre "quién gana" el mismo evento.
    """
    import re

    q_lower = question.lower()
    is_winner_q = any(re.search(p, q_lower) for p in _COMPETITION_PATTERNS)
    if not is_winner_q:
        return False

    # Extrae palabras clave del evento (excluyendo el sujeto)
    event_words = set(re.findall(r"\b[a-z]{4,}\b", q_lower)) - {
        "will", "does", "have", "been", "with", "this", "from", "when",
        "winner", "champion", "president", "minister", "nominee",
    }

    for sim, m in candidates:
        if sim < 0.5:
            continue
        candidate_lower = m["question"].lower()
        candidate_is_winner = any(re.search(p, candidate_lower)
                                  for p in _COMPETITION_PATTERNS)
        if not candidate_is_winner:
            continue
        candidate_words = set(re.findall(r"\b[a-z]{4,}\b", candidate_lower)) - {
            "will", "does", "have", "been", "with", "this", "from", "when",
            "winner", "champion", "president", "minister", "nominee",
        }
        # Si comparten palabras del evento pero hablan de sujetos distintos
        overlap = len(event_words & candidate_words) / max(len(event_words | candidate_words), 1)
        if overlap > 0.4:
            # Heurística: si ambos dicen "gana X" para distintos X → contradicción
            if sim > 0.5 and abs(m["price"] - 0.5) > 0.1:
                return True
    return False


# Instancia global (se reutiliza entre escaneos)
cross_market_analyzer = CrossMarketAnalyzer()
