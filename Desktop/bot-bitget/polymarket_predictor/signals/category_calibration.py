"""
Calibración específica por categoría de mercado.

Investigación empírica sobre prediction markets muestra sesgos distintos
por categoría:

  CRYPTO:     - Longshot bias fuerte (gente apuesta en moonshots)
              - Mercados al 5-20% resuelven YES menos de lo esperado
              - Mercados al 80-95% a veces se reversan por noticias
              - Corrección: comprimir hacia 0.5 en extremos

  POLÍTICA:   - Bien calibrado en general (mucho dinero = precio eficiente)
              - Sesgo leve hacia incumbentes (status quo bias)
              - Eventos binarios simples (gana/pierde) son más fiables
              - Corrección: pequeño ajuste hacia resultados históricos

  ELECCIONES: - Más precisos cuanto más cerca de la fecha
              - Efecto "favorite-longshot": favoritos ligeramente sobreestimados
              - Corrección: ligera compresión de extremos + decay temporal

  FINANZAS:   - Fed decisions: mercados muy bien calibrados (insiders)
              - Stock targets: tendencia al optimismo (sesgo alcista)
              - IPO/earnings: alta incertidumbre, penalizar confianza
              - Corrección: sesgo alcista → pequeña corrección bajista

  DEFAULT:    - Sin corrección específica, aplica base rate general
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Palabras clave para detección de categoría ───────────────────────

_CATEGORY_KEYWORDS = {
    "crypto": [
        "bitcoin", "btc", "ethereum", "eth", "crypto", "solana", "sol",
        "xrp", "ripple", "cardano", "ada", "polygon", "matic", "avalanche",
        "avax", "chainlink", "link", "dogecoin", "doge", "shiba", "bnb",
        "coinbase", "binance", "defi", "nft", "blockchain", "web3", "token",
        "altcoin", "stablecoin", "usdc", "usdt", "halving", "etf bitcoin",
        "crypto regulation", "sec crypto",
    ],
    "elecciones": [
        "election", "elections", "vote", "voting", "ballot", "polls",
        "primary", "primaries", "runoff", "referendum", "candidate",
        "gubernatorial", "congressional", "senate race", "house race",
        "presidential race", "swing state", "electoral", "turnout",
        "incumbent", "approval rating", "polling",
    ],
    "politica": [
        "trump", "biden", "harris", "congress", "senate", "house",
        "republican", "democrat", "president", "prime minister",
        "parliament", "government", "minister", "legislation", "bill",
        "white house", "executive order", "supreme court", "court",
        "ukraine", "russia", "china", "nato", "iran", "israel", "gaza",
        "sanctions", "tariff", "trade war", "military", "ceasefire",
        "treaty", "summit", "diplomacy",
    ],
    "finanzas": [
        "fed", "federal reserve", "interest rate", "rate cut", "rate hike",
        "inflation", "cpi", "pce", "gdp", "recession", "unemployment",
        "jobs report", "payroll", "stock", "s&p", "sp500", "nasdaq", "dow",
        "earnings", "ipo", "bonds", "treasury", "yield", "oil", "gold",
        "silver", "dollar", "euro", "yen", "currency", "imf", "world bank",
        "hedge fund", "jpmorgan", "goldman", "blackrock", "bank", "debt",
    ],
}

# ── Parámetros de calibración por categoría ──────────────────────────
# Formato: (compression, bias, confidence_factor, description)
# compression: cuánto comprimir hacia 0.5 (0=nada, 1=todo a 0.5)
# bias: ajuste absoluto (positivo = ajustar hacia YES, negativo hacia NO)
# confidence_factor: multiplicador de confianza base

_CATEGORY_PARAMS = {
    "crypto": {
        "description":        "Crypto - alta volatilidad, longshot bias fuerte",
        "compression_low":    0.15,   # mercados <30%: comprimir hacia 0.5
        "compression_high":   0.10,   # mercados >70%: comprimir ligeramente
        "bias":               0.0,
        "confidence_factor":  0.85,   # reducir confianza (más volátil)
        "longshot_threshold": 0.15,   # por debajo → overpriced
        "longshot_discount":  0.7,    # multiplicador del precio en longshots
    },
    "elecciones": {
        "description":        "Elecciones - bien calibradas, favoritos ligeramente overpriced",
        "compression_low":    0.05,
        "compression_high":   0.08,   # comprime favoritos >75%
        "bias":               0.0,
        "confidence_factor":  1.05,   # elecciones tienen buen track record
        "longshot_threshold": 0.10,
        "longshot_discount":  0.85,
    },
    "politica": {
        "description":        "Política general - status quo bias",
        "compression_low":    0.08,
        "compression_high":   0.05,
        "bias":               0.02,   # leve sesgo hacia status quo (YES = cambio es raro)
        "confidence_factor":  0.95,
        "longshot_threshold": 0.12,
        "longshot_discount":  0.80,
    },
    "finanzas": {
        "description":        "Finanzas - sesgo alcista, Fed bien calibrado",
        "compression_low":    0.05,
        "compression_high":   0.07,
        "bias":              -0.02,   # corrección del sesgo alcista
        "confidence_factor":  0.90,
        "longshot_threshold": 0.10,
        "longshot_discount":  0.85,
    },
    "default": {
        "description":        "Categoría por defecto",
        "compression_low":    0.05,
        "compression_high":   0.05,
        "bias":               0.0,
        "confidence_factor":  1.0,
        "longshot_threshold": 0.10,
        "longshot_discount":  0.85,
    },
}


def categorize_market(question: str) -> str:
    """
    Clasifica un mercado en su categoría basándose en la pregunta.
    Retorna: 'crypto' | 'elecciones' | 'politica' | 'finanzas' | 'default'
    """
    q_lower = question.lower()
    q_words = set(re.findall(r"\b\w+\b", q_lower))

    scores = {}
    for category, keywords in _CATEGORY_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if " " in kw:   # frase completa
                if kw in q_lower:
                    score += 2
            elif kw in q_words:
                score += 1
        scores[category] = score

    best_cat = max(scores, key=scores.get)
    if scores[best_cat] == 0:
        return "default"

    return best_cat


def calibrate_by_category(price: float, category: str,
                           days_left: Optional[float] = None,
                           confidence: float = 0.5) -> tuple[float, float]:
    """
    Aplica corrección de calibración específica a la categoría.

    Retorna: (calibrated_price, adjusted_confidence)
    """
    params = _CATEGORY_PARAMS.get(category, _CATEGORY_PARAMS["default"])

    calibrated = price

    # Corrección por longshot bias: mercados muy baratos tienden a estar overpriced
    if price < params["longshot_threshold"]:
        calibrated = price * params["longshot_discount"]

    # Compresión hacia 0.5 según la zona del precio
    elif price < 0.30:
        # Zona baja: comprimimos hacia 0.5 (mercados baratos suelen estar sobreestimados)
        calibrated = price + (0.5 - price) * params["compression_low"]
    elif price > 0.70:
        # Zona alta: comprimimos ligeramente (favoritos pueden estar overpriced)
        calibrated = price - (price - 0.5) * params["compression_high"]

    # Aplica bias de categoría
    calibrated += params["bias"]
    calibrated = max(0.01, min(0.99, calibrated))

    # Ajuste de confianza por categoría
    adj_conf = confidence * params["confidence_factor"]

    # Para elecciones: confianza crece cuanto más cerca de la fecha
    if category == "elecciones" and days_left is not None:
        if days_left < 7:
            adj_conf = min(0.95, adj_conf * 1.20)
        elif days_left < 30:
            adj_conf = min(0.90, adj_conf * 1.10)

    # Para crypto: confianza decrece si el mercado está lejos de resolución
    if category == "crypto" and days_left is not None:
        if days_left > 60:
            adj_conf = adj_conf * 0.85

    return round(calibrated, 4), round(min(0.95, adj_conf), 4)


def get_category_description(category: str) -> str:
    """Retorna descripción human-readable de la categoría."""
    return _CATEGORY_PARAMS.get(category, _CATEGORY_PARAMS["default"])["description"]
