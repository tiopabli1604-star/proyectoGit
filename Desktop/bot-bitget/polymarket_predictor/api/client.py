import time
import hashlib
import hmac
import base64
import requests
from typing import Optional
from config import CLOB_API_URL, GAMMA_API_URL, POLY_API_KEY, POLY_SECRET, POLY_PASSPHRASE


class PolymarketClient:
    """Cliente REST para la API CLOB de Polymarket."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------
    # Autenticación (L1 HMAC) — sólo necesaria para endpoints privados
    # ------------------------------------------------------------------
    def _auth_headers(self, method: str, path: str, body: str = "") -> dict:
        if not POLY_API_KEY:
            return {}
        ts = str(int(time.time() * 1000))
        msg = ts + method.upper() + path + body
        sig = base64.b64encode(
            hmac.new(POLY_SECRET.encode(), msg.encode(), hashlib.sha256).digest()
        ).decode()
        return {
            "POLY-API-KEY": POLY_API_KEY,
            "POLY-SIGNATURE": sig,
            "POLY-TIMESTAMP": ts,
            "POLY-PASSPHRASE": POLY_PASSPHRASE,
        }

    def _get(self, base: str, path: str, params: dict = None) -> dict | list:
        r = self.session.get(f"{base}{path}", params=params, timeout=10)
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Endpoints de mercados (Gamma API — sin auth)
    # ------------------------------------------------------------------
    def get_markets(self, limit: int = 100, offset: int = 0,
                    active: bool = True) -> list[dict]:
        """Devuelve lista de mercados activos con metadatos."""
        data = self._get(GAMMA_API_URL, "/markets", params={
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": "false",
        })
        if isinstance(data, list):
            return data
        return data.get("data", data.get("markets", []))

    def get_events(self, limit: int = 100, offset: int = 0) -> list[dict]:
        """Devuelve eventos activos con todos sus sub-mercados."""
        data = self._get(GAMMA_API_URL, "/events", params={
            "limit": limit,
            "offset": offset,
            "active": "true",
            "closed": "false",
        })
        if isinstance(data, list):
            return data
        return data.get("data", [])

    def get_all_events_markets(self, keywords: list[str] = None,
                               max_pages: int = 30) -> list[dict]:
        """
        Obtiene todos los mercados via endpoint de EVENTOS.
        Esto da acceso a sub-mercados que no aparecen en /markets.
        """
        all_markets = []
        kw_lower = [k.lower() for k in keywords] if keywords else []

        for page in range(max_pages):
            offset = page * 100
            try:
                events = self.get_events(limit=100, offset=offset)
            except Exception:
                break
            if not events:
                break

            for event in events:
                title = (event.get("title") or "").lower()
                desc  = (event.get("description") or "").lower()

                for m in event.get("markets", []):
                    question = (m.get("question") or "").lower()
                    text = question + " " + title + " " + desc
                    if not kw_lower or any(kw in text for kw in kw_lower):
                        # Enriquece el mercado con datos del evento
                        m.setdefault("_event_title", event.get("title", ""))
                        all_markets.append(m)

            if len(events) < 100:
                break

        return all_markets

    def get_all_markets(self, keywords: list[str] = None,
                        max_pages: int = 20) -> list[dict]:
        """
        Pagina todos los mercados activos y filtra por palabras clave.
        keywords: lista de palabras a buscar en la pregunta del mercado.
        max_pages: límite de páginas (100 mercados por página).
        """
        all_markets = []
        kw_lower = [k.lower() for k in keywords] if keywords else []

        for page in range(max_pages):
            offset = page * 100
            batch = self.get_markets(limit=100, offset=offset)
            if not batch:
                break
            if kw_lower:
                batch = [
                    m for m in batch
                    if any(kw in (m.get("question", "") + " " +
                                  m.get("description", "")).lower()
                           for kw in kw_lower)
                ]
            all_markets.extend(batch)
            if len(batch) < 100:
                break  # última página

        return all_markets

    def get_market(self, condition_id: str) -> dict:
        # Primero intenta por ID directo
        try:
            return self._get(GAMMA_API_URL, f"/markets/{condition_id}")
        except Exception:
            pass
        # Si falla, busca por conditionId en la lista
        data = self._get(GAMMA_API_URL, "/markets", params={
            "condition_ids": condition_id,
            "limit": 1,
        })
        markets = data if isinstance(data, list) else data.get("data", [])
        if markets:
            return markets[0]
        # Último recurso: paginar y buscar
        for offset in range(0, 3000, 100):
            batch = self.get_markets(limit=100, offset=offset)
            if not batch:
                break
            for m in batch:
                if m.get("conditionId") == condition_id:
                    return m
        return {}

    # ------------------------------------------------------------------
    # Endpoints CLOB (libro de órdenes y trades)
    # ------------------------------------------------------------------
    def get_orderbook(self, token_id: str) -> dict:
        """Libro de órdenes completo para un token (YES/NO side)."""
        return self._get(CLOB_API_URL, "/book", params={"token_id": token_id})

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Precio mid del token (0-1)."""
        data = self._get(CLOB_API_URL, "/midpoint", params={"token_id": token_id})
        val = data.get("mid")
        return float(val) if val is not None else None

    def get_price(self, token_id: str, side: str = "BUY") -> Optional[float]:
        """Mejor precio disponible para BUY o SELL."""
        data = self._get(CLOB_API_URL, "/price",
                         params={"token_id": token_id, "side": side})
        val = data.get("price")
        return float(val) if val is not None else None

    def get_trades(self, market: str, limit: int = 200) -> list[dict]:
        """Historial de trades recientes de un mercado (condition_id)."""
        data = self._get(CLOB_API_URL, "/trades",
                         params={"market": market, "limit": limit})
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data", []) or []
        return []

    def get_spread(self, token_id: str) -> Optional[float]:
        """Spread bid-ask normalizado (0-1)."""
        data = self._get(CLOB_API_URL, "/spread", params={"token_id": token_id})
        val = data.get("spread")
        return float(val) if val is not None else None

    def get_last_trade_price(self, token_id: str) -> Optional[float]:
        data = self._get(CLOB_API_URL, "/last-trade-price",
                         params={"token_id": token_id})
        val = data.get("price")
        return float(val) if val is not None else None
