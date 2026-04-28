"""
Señales externas: fuentes de información fuera del mercado.

El edge real viene de saber algo que el mercado aún no ha incorporado.
Fuentes usadas (todas gratuitas):
  - Metaculus API: probabilidades de la comunidad experta (~5000 preguntas activas)
  - Manifold Markets API: precios de otro mercado de predicción (arbitraje)
  - RSS News: titulares recientes de Reuters/AP para detectar noticias frescas
"""

import time
import logging
import re
import hashlib
from typing import Optional
from difflib import SequenceMatcher
from datetime import datetime, timezone, timedelta

import requests

logger = logging.getLogger(__name__)

_CACHE: dict = {}          # {key: (value, timestamp)}
_CACHE_TTL  = 300          # 5 minutos
_SESSION    = requests.Session()
_SESSION.headers["User-Agent"] = "PolymarketBot/2.0 (research)"


def _cache_get(key: str):
    if key in _CACHE:
        val, ts = _CACHE[key]
        if time.time() - ts < _CACHE_TTL:
            return val
    return None


def _cache_set(key: str, val):
    _CACHE[key] = (val, time.time())


def _get(url: str, params: dict = None, timeout: int = 8):
    try:
        r = _SESSION.get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.debug(f"External GET {url}: {e}")
        return None


def _similarity(a: str, b: str) -> float:
    """Similitud de texto entre 0 y 1."""
    a = re.sub(r"[^\w\s]", "", a.lower())
    b = re.sub(r"[^\w\s]", "", b.lower())
    return SequenceMatcher(None, a, b).ratio()


# ── Kalshi ───────────────────────────────────────────────────────────

class KalshiSignal:
    """
    Kalshi: mercado de predicción regulado de EE.UU. API pública de lectura.
    Sus precios son independientes de Polymarket → señal de arbitraje real.
    Kalshi usa centavos (0-100), Polymarket usa decimales (0-1).
    """
    BASE = "https://api.elections.kalshi.com/trade-api/v2"
    MIN_SIMILARITY = 0.30

    def search(self, query: str, limit: int = 10) -> list[dict]:
        cache_key = f"kalshi_{hashlib.md5(query.encode()).hexdigest()}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        data = _get(f"{self.BASE}/markets", params={
            "limit":   limit,
            "status":  "open",
        })
        results = []
        if data and isinstance(data.get("markets"), list):
            q_lower = query.lower()
            for m in data["markets"]:
                title = m.get("title", "")
                # Filtra solo los que son relevantes al query
                if not any(w in title.lower() for w in q_lower.split()[:3]):
                    continue
                # Kalshi precios en cents ($0.01) → divide por 100
                price_raw = m.get("last_price_dollars") or m.get("yes_ask_dollars") or 0
                try:
                    prob = float(price_raw)
                    if prob > 1.0:
                        prob /= 100.0
                except Exception:
                    prob = None
                if prob is None or prob <= 0:
                    continue
                results.append({
                    "ticker": m.get("ticker"),
                    "title":  title,
                    "prob":   round(prob, 4),
                    "volume": float(m.get("volume_24h_fp", 0) or 0),
                })
        _cache_set(cache_key, results)
        return results

    def find_match(self, polymarket_question: str) -> Optional[dict]:
        words = [w for w in re.findall(r"\b\w{4,}\b", polymarket_question.lower())
                 if w not in {"will", "does", "have", "been", "that", "with",
                              "this", "from", "they", "what", "when", "which"}]
        query = " ".join(words[:5])
        if not query:
            return None

        candidates = self.search(query)
        best = None
        best_score = self.MIN_SIMILARITY

        for c in candidates:
            sim = _similarity(polymarket_question, c["title"])
            if sim > best_score:
                best_score = sim
                best = {**c, "similarity": round(sim, 3)}

        return best


# ── Manifold Markets ─────────────────────────────────────────────────

class ManifoldSignal:
    """
    Manifold Markets: otro mercado de predicción. Sus precios divergen
    de Polymarket con frecuencia → oportunidad de arbitraje/información.
    API completamente gratuita.
    """
    BASE = "https://api.manifold.markets/v0"

    def search(self, query: str, limit: int = 5) -> list[dict]:
        cache_key = f"mani_{hashlib.md5(query.encode()).hexdigest()}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        data = _get(f"{self.BASE}/search-markets", params={
            "term":  query[:100],
            "limit": limit,
            "sort":  "liquidity",
        })
        results = []
        if isinstance(data, list):
            for m in data:
                prob = m.get("probability")
                results.append({
                    "id":    m.get("id"),
                    "title": m.get("question", ""),
                    "prob":  float(prob) if prob is not None else None,
                    "url":   m.get("url", ""),
                    "volume": m.get("volume", 0),
                })
        _cache_set(cache_key, results)
        return results

    def find_match(self, polymarket_question: str) -> Optional[dict]:
        words = [w for w in re.findall(r"\b\w{4,}\b", polymarket_question.lower())
                 if w not in {"will", "does", "have", "been", "that", "with",
                              "this", "from", "they", "what", "when", "which"}]
        query = " ".join(words[:6])
        if not query:
            return None

        candidates = self.search(query)
        best = None
        best_score = 0.3

        for c in candidates:
            if c["prob"] is None:
                continue
            sim = _similarity(polymarket_question, c["title"])
            if sim > best_score:
                best_score = sim
                best = {**c, "similarity": round(sim, 3)}

        return best


# ── News Sentiment ────────────────────────────────────────────────────

_RSS_FEEDS = [
    "https://feeds.reuters.com/reuters/topNews",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
]

_POS_WORDS = {
    "win", "wins", "won", "victory", "approved", "passes", "signed",
    "confirmed", "elected", "peace", "ceasefire", "deal", "agreement",
    "rises", "surges", "gains", "breakthrough", "positive", "success",
}
_NEG_WORDS = {
    "lose", "loses", "lost", "defeat", "rejected", "fails", "blocked",
    "vetoed", "fired", "resigns", "resignation", "crisis", "crash",
    "falls", "drops", "collapses", "negative", "failed", "ban",
}


def _parse_rss_headlines(feed_url: str) -> list[str]:
    """Parsea titulares de un feed RSS sin librerías externas."""
    data = None
    try:
        r = _SESSION.get(feed_url, timeout=6)
        data = r.text
    except Exception:
        return []
    titles = re.findall(r"<title><!\[CDATA\[(.*?)\]\]></title>", data)
    if not titles:
        titles = re.findall(r"<title>(.*?)</title>", data)
    return [t.strip() for t in titles[2:20]]  # salta el título del feed


def news_sentiment(topic_keywords: list[str], max_age_hours: int = 6) -> dict:
    """
    Busca noticias recientes relacionadas con las keywords del mercado.
    Retorna {'score': float [-1,1], 'headlines': list, 'count': int}

    score > 0 → noticias positivas para YES
    score < 0 → noticias negativas para YES
    """
    cache_key = f"news_{'_'.join(sorted(topic_keywords[:3]))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    kw_lower = [k.lower() for k in topic_keywords]
    matched_headlines = []
    sentiment_scores = []

    for feed in _RSS_FEEDS:
        headlines = _parse_rss_headlines(feed)
        for h in headlines:
            h_lower = h.lower()
            if any(kw in h_lower for kw in kw_lower):
                matched_headlines.append(h)
                words = set(h_lower.split())
                pos = len(words & _POS_WORDS)
                neg = len(words & _NEG_WORDS)
                if pos + neg > 0:
                    sentiment_scores.append((pos - neg) / (pos + neg))
                else:
                    sentiment_scores.append(0.0)

    score = float(sum(sentiment_scores) / len(sentiment_scores)) if sentiment_scores else 0.0
    result = {
        "score":     round(score, 3),
        "headlines": matched_headlines[:5],
        "count":     len(matched_headlines),
    }
    _cache_set(cache_key, result)
    return result


# ── Resumen externo compuesto ─────────────────────────────────────────

_kalshi  = KalshiSignal()
_manifold = ManifoldSignal()


def get_external_signal(question: str,
                        topic_keywords: list[str] = None) -> dict:
    """
    Combina todas las señales externas para un mercado.
    Retorna:
      {
        'external_prob':  float | None,   # probabilidad externa consenso
        'source_weight':  float,           # cuánto confiar en external_prob (0-1)
        'kalshi':         dict | None,     # precio en Kalshi
        'manifold':       dict | None,     # precio en Manifold
        'news':           dict,            # sentimiento de noticias
      }
    """
    result = {
        "external_prob": None,
        "source_weight": 0.0,
        "kalshi":        None,
        "manifold":      None,
        "news":          {"score": 0.0, "count": 0, "headlines": []},
    }

    # Manifold Markets (alta liquidez en política/crypto/deportes)
    try:
        mani = _manifold.find_match(question)
        if mani and mani.get("prob") is not None:
            result["manifold"] = mani
            result["external_prob"] = mani["prob"]
            result["source_weight"] = min(0.6, mani["similarity"] * 0.95)
    except Exception as e:
        logger.debug(f"Manifold error: {e}")

    # Kalshi (mercado regulado EE.UU. — precios independientes)
    try:
        kal = _kalshi.find_match(question)
        if kal and kal.get("prob") is not None:
            result["kalshi"] = kal
            if result["external_prob"] is None:
                result["external_prob"] = kal["prob"]
                result["source_weight"] = min(0.7, kal["similarity"] * 1.1)
            else:
                # Promedia: Kalshi tiene ligeramente más confianza (regulado)
                result["external_prob"] = (
                    result["external_prob"] * 0.4 + kal["prob"] * 0.6
                )
                result["source_weight"] = min(0.75,
                    result["source_weight"] * 0.4 + min(0.7, kal["similarity"] * 1.1) * 0.6
                )
    except Exception as e:
        logger.debug(f"Kalshi error: {e}")

    # News sentiment (señal de dirección pero no magnitud)
    if topic_keywords:
        try:
            result["news"] = news_sentiment(topic_keywords[:4])
        except Exception as e:
            logger.debug(f"News error: {e}")

    return result
