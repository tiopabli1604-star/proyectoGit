"""
Ejecutor de órdenes en Polymarket via CLOB API.

Requiere en .env:
  POLY_API_KEY        → de Polymarket Settings → API Keys
  POLY_SECRET         → ídem
  POLY_PASSPHRASE     → ídem
  POLY_PRIVATE_KEY    → clave privada de tu wallet Polygon (0x...)
  POLY_CHAIN_ID       → 137 (Polygon mainnet) o 80002 (testnet)

IMPORTANTE: Nunca compartas POLY_PRIVATE_KEY con nadie.
"""

import os
import logging
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

POLY_API_KEY     = os.getenv("POLY_API_KEY", "")
POLY_SECRET      = os.getenv("POLY_SECRET", "")
POLY_PASSPHRASE  = os.getenv("POLY_PASSPHRASE", "")
POLY_PRIVATE_KEY = os.getenv("POLY_PRIVATE_KEY", "")
POLY_CHAIN_ID    = int(os.getenv("POLY_CHAIN_ID", "137"))

CLOB_URL = "https://clob.polymarket.com"

# Límites de seguridad
MAX_ORDER_USD    = float(os.getenv("MAX_ORDER_USD", "50"))    # máximo por orden
MAX_DAILY_USD    = float(os.getenv("MAX_DAILY_USD", "200"))   # máximo por día
MIN_EDGE_TO_BUY  = float(os.getenv("MIN_EDGE_TO_BUY", "0.06"))  # edge mínimo 6%


def is_configured() -> bool:
    return bool(POLY_API_KEY and POLY_SECRET and POLY_PASSPHRASE and POLY_PRIVATE_KEY)


def _get_client():
    """Crea cliente CLOB autenticado."""
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
        creds = ApiCreds(
            api_key        = POLY_API_KEY,
            api_secret     = POLY_SECRET,
            api_passphrase = POLY_PASSPHRASE,
        )
        client = ClobClient(
            host       = CLOB_URL,
            chain_id   = POLY_CHAIN_ID,
            key        = POLY_PRIVATE_KEY,
            creds      = creds,
            signature_type = 0,
        )
        return client
    except ImportError:
        raise ImportError("Instala py-clob-client: pip install py-clob-client")
    except Exception as e:
        raise RuntimeError(f"Error al crear cliente CLOB: {e}")


def get_balance() -> Optional[float]:
    """Retorna el balance de USDC disponible en Polymarket."""
    if not is_configured():
        return None
    try:
        client = _get_client()
        balance = client.get_balance()
        return float(balance)
    except Exception as e:
        logger.error(f"Error obteniendo balance: {e}")
        return None


def place_market_order(token_id: str, side: str, amount_usd: float,
                       condition_id: str = "", question: str = "") -> dict:
    """
    Ejecuta una orden de mercado en Polymarket.

    token_id:   ID del token YES o NO (de clobTokenIds[0] o [1])
    side:       'BUY' o 'SELL'
    amount_usd: cantidad en USDC

    Retorna dict con resultado: {ok, order_id, filled, error}
    """
    if not is_configured():
        return {"ok": False, "error": "API de Polymarket no configurada. Añade POLY_PRIVATE_KEY al .env"}

    if amount_usd > MAX_ORDER_USD:
        return {"ok": False, "error": f"Orden demasiado grande: ${amount_usd:.2f} > máximo ${MAX_ORDER_USD:.2f}"}

    try:
        from py_clob_client.clob_types import MarketOrderArgs, OrderType
        client = _get_client()

        order_args = MarketOrderArgs(
            token_id   = token_id,
            amount     = amount_usd,
        )
        signed_order = client.create_market_order(order_args)
        resp = client.post_order(signed_order, OrderType.FOK)

        if resp.get("success"):
            filled = float(resp.get("size_matched", 0))
            logger.info(f"Orden ejecutada: {side} ${amount_usd:.2f} en {condition_id[:20]} — filled: {filled}")
            return {
                "ok":       True,
                "order_id": resp.get("orderID", ""),
                "filled":   filled,
                "status":   resp.get("status", ""),
            }
        else:
            err = resp.get("error", str(resp))
            logger.error(f"Orden rechazada: {err}")
            return {"ok": False, "error": err}

    except Exception as e:
        logger.error(f"Error ejecutando orden: {e}")
        return {"ok": False, "error": str(e)}


def buy_yes(condition_id: str, token_id: str, amount_usd: float,
            question: str = "") -> dict:
    """Compra tokens YES en un mercado."""
    return place_market_order(token_id, "BUY", amount_usd,
                               condition_id=condition_id, question=question)


def buy_no(condition_id: str, token_id_no: str, amount_usd: float,
           question: str = "") -> dict:
    """Compra tokens NO en un mercado (usa el segundo token del par)."""
    return place_market_order(token_id_no, "BUY", amount_usd,
                               condition_id=condition_id, question=question)


def get_open_orders() -> list:
    """Lista órdenes abiertas en Polymarket."""
    if not is_configured():
        return []
    try:
        client = _get_client()
        orders = client.get_orders()
        return orders if isinstance(orders, list) else []
    except Exception as e:
        logger.error(f"Error obteniendo órdenes: {e}")
        return []


def cancel_order(order_id: str) -> bool:
    """Cancela una orden abierta."""
    if not is_configured():
        return False
    try:
        client = _get_client()
        client.cancel(order_id)
        return True
    except Exception as e:
        logger.error(f"Error cancelando orden {order_id}: {e}")
        return False
