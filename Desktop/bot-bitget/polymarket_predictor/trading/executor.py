"""
Ejecutor de órdenes en Polymarket via CLOB API v2.

Requiere en .env:
  POLY_API_KEY        → de Polymarket Settings → API Keys
  POLY_SECRET         → ídem
  POLY_PASSPHRASE     → ídem
  POLY_PRIVATE_KEY    → clave privada de tu wallet Polygon (0x...)
  POLY_CHAIN_ID       → 137 (Polygon mainnet)

Modo EOA puro (sig_type=0): maker=EOA, signer=EOA. Coincide con el API key.
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

MAX_ORDER_USD = float(os.getenv("MAX_ORDER_USD", "50"))


def is_configured() -> bool:
    cfg = bool(POLY_API_KEY and POLY_SECRET and POLY_PASSPHRASE and POLY_PRIVATE_KEY)
    logger.info(f"is_configured={cfg} api_key={POLY_API_KEY[:8]}... chain={POLY_CHAIN_ID}")
    return cfg


def _get_client():
    """Crea cliente CLOB v2 en modo EOA (sig_type=0)."""
    try:
        from py_clob_client_v2.client import ClobClient
        from py_clob_client_v2.clob_types import ApiCreds
        from py_clob_client_v2.constants import POLYGON
    except ImportError:
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
            POLYGON = 137
        except ImportError:
            raise ImportError("Instala py-clob-client-v2: pip install py-clob-client-v2")

    creds = ApiCreds(
        api_key        = POLY_API_KEY,
        api_secret     = POLY_SECRET,
        api_passphrase = POLY_PASSPHRASE,
    )
    client = ClobClient(
        host           = CLOB_URL,
        key            = POLY_PRIVATE_KEY,
        chain_id       = POLY_CHAIN_ID,
        creds          = creds,
        signature_type = 0,   # EOA: maker=EOA, signer=EOA — coincide con API key
        funder         = None,
    )
    logger.info(f"Cliente CLOB EOA: {client.get_address()}")
    return client


def get_balance() -> Optional[float]:
    """Retorna el balance de USDC disponible en Polymarket."""
    if not is_configured():
        return None
    try:
        client = _get_client()
        bal = client.get_balance()
        return float(bal)
    except Exception as e:
        logger.error(f"Error obteniendo balance: {e}")
        return None


def place_market_order(token_id: str, side: str, amount_usd: float,
                       condition_id: str = "", question: str = "",
                       neg_risk: bool = True) -> dict:
    """
    Ejecuta una orden de mercado en Polymarket (modo EOA, sig_type=0).

    token_id:   ID del token YES o NO
    side:       'BUY'
    amount_usd: cantidad en USD
    neg_risk:   True para mercados BTC 5m (neg-risk)
    """
    if not is_configured():
        return {"ok": False, "error": "POLY_API_KEY / POLY_PRIVATE_KEY no configurados"}

    if amount_usd > MAX_ORDER_USD:
        return {"ok": False, "error": f"Orden demasiado grande: ${amount_usd:.2f} > max ${MAX_ORDER_USD:.2f}"}

    try:
        client = _get_client()

        # Importar desde la librería disponible
        try:
            from py_clob_client_v2.clob_types import MarketOrderArgs, OrderType
        except ImportError:
            from py_clob_client.clob_types import MarketOrderArgs, OrderType

        import inspect
        sig_params = inspect.signature(MarketOrderArgs.__init__).parameters
        order_kwargs: dict = {"token_id": token_id, "amount": amount_usd, "side": side}
        if "neg_risk" in sig_params:
            order_kwargs["neg_risk"] = neg_risk

        order_args   = MarketOrderArgs(**order_kwargs)
        signed_order = client.create_market_order(order_args)

        # Log diagnóstico
        od = signed_order.__dict__ if hasattr(signed_order, "__dict__") else {}
        logger.info(
            f"Orden: sigType={od.get('signatureType','?')} side={od.get('side','?')} "
            f"maker={str(od.get('maker','?'))[:20]} signer={str(od.get('signer','?'))[:20]}"
        )

        resp = client.post_order(signed_order, OrderType.FOK)

        if resp.get("success"):
            filled = float(resp.get("size_matched", 0))
            logger.info(f"✅ Orden ejecutada: {side} ${amount_usd:.2f} filled={filled}")
            return {
                "ok":       True,
                "order_id": resp.get("orderID", ""),
                "filled":   filled,
                "status":   resp.get("status", ""),
            }

        err = resp.get("errorMsg") or resp.get("error") or str(resp)
        logger.error(f"Orden rechazada: {err}")
        return {"ok": False, "error": err}

    except Exception as e:
        logger.error(f"Error ejecutando orden: {e}")
        return {"ok": False, "error": str(e)}


def buy_yes(condition_id: str, token_id: str, amount_usd: float,
            question: str = "", neg_risk: bool = True) -> dict:
    return place_market_order(token_id, "BUY", amount_usd,
                               condition_id=condition_id, question=question,
                               neg_risk=neg_risk)


def buy_no(condition_id: str, token_id_no: str, amount_usd: float,
           question: str = "", neg_risk: bool = True) -> dict:
    return place_market_order(token_id_no, "BUY", amount_usd,
                               condition_id=condition_id, question=question,
                               neg_risk=neg_risk)


def get_open_orders() -> list:
    if not is_configured():
        return []
    try:
        orders = _get_client().get_orders()
        return orders if isinstance(orders, list) else []
    except Exception as e:
        logger.error(f"Error obteniendo órdenes: {e}")
        return []


def cancel_order(order_id: str) -> bool:
    if not is_configured():
        return False
    try:
        _get_client().cancel(order_id)
        return True
    except Exception as e:
        logger.error(f"Error cancelando orden {order_id}: {e}")
        return False
