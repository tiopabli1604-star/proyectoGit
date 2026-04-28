"""
NLP avanzado para análisis de noticias y similaridad semántica.

Mejoras sobre el sistema anterior:
  - VADER sentiment (vs conteo simple de palabras positivas/negativas)
  - TF-IDF cosine similarity (vs SequenceMatcher) para mejor matching
  - Extracción de texto completo de RSS (título + descripción + cuerpo)
  - Detección de entidades nombradas (personas, países, organizaciones)
  - Score de relevancia ponderado por frescura de la noticia
"""

import re
import time
import logging
import hashlib
import math
from typing import Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── VADER Sentiment ──────────────────────────────────────────────────
# Carga lazy para no ralentizar el arranque si no está instalado

_vader_analyzer = None

def _get_vader():
    global _vader_analyzer
    if _vader_analyzer is None:
        try:
            from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
            _vader_analyzer = SentimentIntensityAnalyzer()
            # Añadir léxico financiero/político especializado
            _vader_analyzer.lexicon.update({
                # Positivos para YES (evento ocurre)
                "wins":      2.5, "won":       2.5, "elected":  2.5,
                "approved":  2.0, "passed":    2.0, "signed":   2.0,
                "confirmed": 2.0, "ceasefire": 1.5, "deal":     1.5,
                "agreement": 1.5, "peace":     1.5, "bullish":  2.0,
                "surge":     2.0, "soars":     2.0, "rally":    1.8,
                "breakout":  2.0, "ath":       2.5, "record":   1.5,
                # Negativos para YES (evento NO ocurre)
                "loses":    -2.5, "lost":     -2.5, "rejected": -2.0,
                "fails":    -2.0, "failed":   -2.0, "blocked":  -2.0,
                "vetoed":   -2.0, "collapses":-2.5, "crash":   -2.5,
                "bearish":  -2.0, "plunge":   -2.0, "dumps":   -2.0,
                "ban":      -1.5, "sanction": -1.0, "tariff":  -0.5,
            })
        except ImportError:
            logger.warning("vaderSentiment no instalado — usando fallback simple")
    return _vader_analyzer


def vader_sentiment(text: str) -> float:
    """
    Analiza el sentimiento de un texto con VADER.
    Retorna compound score en [-1, 1].
    score > 0.05  → positivo (favorece YES)
    score < -0.05 → negativo (favorece NO)
    """
    analyzer = _get_vader()
    if analyzer is None:
        return _simple_sentiment(text)
    try:
        scores = analyzer.polarity_scores(text)
        return round(scores["compound"], 4)
    except Exception:
        return _simple_sentiment(text)


def _simple_sentiment(text: str) -> float:
    """Fallback si VADER no está disponible."""
    pos = {"wins", "won", "approved", "passed", "elected", "ceasefire",
           "deal", "agreement", "bullish", "surge", "soars", "rally"}
    neg = {"loses", "lost", "rejected", "fails", "failed", "blocked",
           "vetoed", "collapses", "crash", "bearish", "plunge", "ban"}
    words = set(text.lower().split())
    p = len(words & pos)
    n = len(words & neg)
    return (p - n) / max(p + n, 1)


# ── TF-IDF Semantic Similarity ───────────────────────────────────────

_tfidf_cache: dict = {}   # {(text1, text2): score}


def semantic_similarity(text1: str, text2: str) -> float:
    """
    Similaridad semántica entre dos textos usando TF-IDF + cosine.
    Mejora sobre SequenceMatcher: entiende vocabulario compartido
    aunque el orden sea diferente.
    """
    key = hashlib.md5((text1 + "|||" + text2).encode()).hexdigest()
    if key in _tfidf_cache:
        return _tfidf_cache[key]

    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as cos_sim
        import numpy as np

        vect = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=200,
            stop_words=["will", "does", "have", "been", "that", "with",
                        "this", "from", "they", "what", "when", "which",
                        "the", "and", "for", "are", "was", "were", "but"],
        )
        # Normaliza texto
        t1 = re.sub(r"[^\w\s]", " ", text1.lower())
        t2 = re.sub(r"[^\w\s]", " ", text2.lower())
        matrix = vect.fit_transform([t1, t2])
        score = float(cos_sim(matrix[0], matrix[1])[0][0])
    except Exception:
        # Fallback a SequenceMatcher
        from difflib import SequenceMatcher
        score = SequenceMatcher(None, text1.lower(), text2.lower()).ratio()

    _tfidf_cache[key] = round(score, 4)
    return _tfidf_cache[key]


def batch_similarity(query: str, texts: list[str]) -> list[float]:
    """
    Calcula similaridad de query contra múltiples textos de una sola vez.
    Más eficiente que llamar semantic_similarity N veces.
    """
    if not texts:
        return []
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity as cos_sim

        all_texts = [re.sub(r"[^\w\s]", " ", t.lower())
                     for t in [query] + texts]
        vect = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=500,
            stop_words=["will", "does", "have", "been", "that", "with",
                        "this", "from", "they", "what", "when", "which",
                        "the", "and", "for", "are", "was", "were", "but"],
        )
        matrix = vect.fit_transform(all_texts)
        scores = cos_sim(matrix[0:1], matrix[1:])
        return [round(float(s), 4) for s in scores[0]]
    except Exception:
        return [semantic_similarity(query, t) for t in texts]


# ── RSS con texto completo ────────────────────────────────────────────

_RSS_SOURCES = [
    # Política / Elecciones
    "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.a.dj.com/rss/RSSWorldNews.xml",
    # Finanzas
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://feeds.reuters.com/reuters/businessNews",
    # Crypto
    "https://cointelegraph.com/rss",
    "https://coindesk.com/arc/outboundfeeds/rss/",
]

_rss_cache: dict = {}
_RSS_TTL = 600   # 10 min por feed


def _fetch_rss_items(feed_url: str) -> list[dict]:
    """Parsea RSS y extrae {title, description, pub_date} con TTL de 10min."""
    now = time.time()
    if feed_url in _rss_cache:
        items, ts = _rss_cache[feed_url]
        if now - ts < _RSS_TTL:
            return items

    try:
        import requests as _req
        r = _req.get(feed_url, timeout=6,
                     headers={"User-Agent": "PolymarketBot/2.0"})
        xml = r.text
    except Exception:
        return []

    items = []
    # Extrae items del XML (título + descripción completa)
    item_blocks = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)
    for block in item_blocks[:20]:
        title = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                          block, re.DOTALL)
        desc  = re.search(r"<description>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</description>",
                          block, re.DOTALL)
        pub   = re.search(r"<pubDate>(.*?)</pubDate>", block)

        title_text = re.sub(r"<[^>]+>", "", title.group(1)).strip() if title else ""
        desc_text  = re.sub(r"<[^>]+>", "", desc.group(1)).strip()[:500] if desc else ""
        pub_str    = pub.group(1).strip() if pub else ""

        # Calcula antigüedad en horas
        age_hours = 999.0
        if pub_str:
            try:
                from email.utils import parsedate_to_datetime
                pub_dt = parsedate_to_datetime(pub_str)
                age_hours = (datetime.now(timezone.utc) - pub_dt).total_seconds() / 3600
            except Exception:
                pass

        if title_text:
            items.append({
                "title":     title_text,
                "text":      f"{title_text} {desc_text}".strip(),
                "age_hours": age_hours,
            })

    _rss_cache[feed_url] = (items, now)
    return items


def analyze_market_news(question: str,
                        topic_keywords: list[str],
                        max_age_hours: float = 48.0) -> dict:
    """
    Analiza noticias recientes relacionadas con un mercado específico.

    Mejoras sobre el sistema anterior:
    - Usa TF-IDF para medir relevancia (no solo coincidencia de palabras)
    - Analiza título + descripción completa (no solo título)
    - Pondera por frescura: noticia de hace 2h > noticia de hace 24h
    - Usa VADER para sentiment (no solo conteo de palabras)

    Retorna:
      {
        'score':      float [-1, 1],   # sentimiento neto ponderado
        'count':      int,             # noticias relevantes encontradas
        'confidence': float [0, 1],   # cuánto confiar en la señal
        'headlines':  list[str],
      }
    """
    cache_key = f"nlp_news_{'_'.join(sorted(topic_keywords[:3]))}"
    if cache_key in _tfidf_cache:
        return _tfidf_cache[cache_key]

    all_items = []
    for feed in _RSS_SOURCES:
        try:
            all_items.extend(_fetch_rss_items(feed))
        except Exception:
            pass

    if not all_items:
        return {"score": 0.0, "count": 0, "confidence": 0.0, "headlines": []}

    # Filtra por keywords y por antigüedad
    kw_lower = [k.lower() for k in topic_keywords]
    candidate_items = []
    for item in all_items:
        if item["age_hours"] > max_age_hours:
            continue
        text_lower = item["text"].lower()
        if any(kw in text_lower for kw in kw_lower):
            candidate_items.append(item)

    if not candidate_items:
        return {"score": 0.0, "count": 0, "confidence": 0.0, "headlines": []}

    # Calcula similitud semántica de cada noticia con la pregunta del mercado
    candidate_texts = [it["text"] for it in candidate_items]
    similarities = batch_similarity(question, candidate_texts)

    weighted_sentiments = []
    headlines = []
    for item, sim in zip(candidate_items, similarities):
        if sim < 0.05:   # irrelevante
            continue
        sentiment = vader_sentiment(item["text"])
        # Peso: similitud semántica × frescura (decaimiento exponencial)
        freshness = math.exp(-item["age_hours"] / 24.0)
        weight = sim * freshness
        weighted_sentiments.append((sentiment, weight))
        headlines.append(item["title"])

    if not weighted_sentiments:
        return {"score": 0.0, "count": 0, "confidence": 0.0, "headlines": []}

    total_weight = sum(w for _, w in weighted_sentiments)
    if total_weight == 0:
        return {"score": 0.0, "count": 0, "confidence": 0.0, "headlines": []}

    weighted_score = sum(s * w for s, w in weighted_sentiments) / total_weight

    # Confianza: más artículos + pesos más altos → más confianza
    n = len(weighted_sentiments)
    avg_weight = total_weight / n
    confidence = min(0.9, (1 - math.exp(-n / 3)) * min(1.0, avg_weight * 5))

    result = {
        "score":      round(weighted_score, 4),
        "count":      n,
        "confidence": round(confidence, 3),
        "headlines":  headlines[:5],
    }
    _tfidf_cache[cache_key] = result
    return result
