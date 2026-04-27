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
        return self._get(GAMMA_API_URL, "/markets", params={
            "limit": limit,
            "offset": offset,
            "active": str(active).lower(),
            "closed": "false",
        })

    def get_market(self, condition_id: str) -> dict:
        return self._get(GAMMA_API_URL, f"/markets/{condition_id}")

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
        return self._get(CLOB_API_URL, "/trades",
                         params={"market": market, "limit": limit})

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
