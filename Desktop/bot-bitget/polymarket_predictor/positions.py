"""
Gestor de posiciones — ahora con SQLite en vez de JSON.

Mantiene la misma API pública para no romper el resto del código.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from database.db import (
    db_add_position, db_get_position, db_get_all_positions,
    db_update_price, db_remove_position, db_mark_alerted,
    db_close_position, db_get_portfolio_summary, init_db,
)

logger = logging.getLogger(__name__)

# Inicializa la DB al importar el módulo
try:
    init_db()
except Exception as e:
    logger.warning(f"No se pudo inicializar DB: {e}")

DEFAULT_PROFIT_TARGET = 0.20
DEFAULT_STOP_LOSS     = -0.25


def add_position(condition_id: str, question: str, action: str,
                 entry_price: float, amount_usd: float,
                 profit_target: float = DEFAULT_PROFIT_TARGET,
                 stop_loss: float = DEFAULT_STOP_LOSS) -> dict:
    pos = db_add_position(condition_id, question, action,
                          entry_price, amount_usd,
                          profit_target, stop_loss)
    logger.info(f"Posición añadida: {condition_id} {action} @ {entry_price:.2%} ${amount_usd}")
    return pos or {}


def remove_position(condition_id: str) -> bool:
    return db_remove_position(condition_id)


def get_all() -> dict:
    """Retorna dict {condition_id: pos} para compatibilidad con código anterior."""
    positions = db_get_all_positions("open")
    return {p["condition_id"]: p for p in positions}


def update_price(condition_id: str, current_price: float) -> Optional[dict]:
    pos = db_get_position(condition_id)
    if not pos:
        return None
    pnl_data = calc_pnl({**pos, "current_price": current_price})
    db_update_price(condition_id, current_price,
                    pnl_data["pnl_usd"], pnl_data["pct_change"])
    return db_get_position(condition_id)


def calc_pnl(pos: dict) -> dict:
    entry   = pos["entry_price"]
    current = pos.get("current_price", entry)
    shares  = pos["shares"]
    action  = pos["action"]

    if action == "YES":
        pnl_usd       = (current - entry) * shares
        pct_change    = (current - entry) / entry if entry > 0 else 0
        current_value = current * shares
    else:
        entry_no   = 1 - entry
        current_no = 1 - current
        pnl_usd       = (entry_no - current_no) * shares
        pct_change    = (entry_no - current_no) / entry_no if entry_no > 0 else 0
        current_value = current_no * shares

    profit_target = pos.get("profit_target", DEFAULT_PROFIT_TARGET)
    stop_loss     = pos.get("stop_loss", DEFAULT_STOP_LOSS)

    return {
        "pnl_usd":             round(pnl_usd, 2),
        "pct_change":          round(pct_change, 4),
        "current_value":       round(current_value, 2),
        "should_alert_profit": (pct_change >= profit_target
                                and not pos.get("alerted_profit")),
        "should_alert_stop":   (pct_change <= stop_loss
                                and not pos.get("alerted_stop")),
    }


def check_alerts(condition_id: str) -> Optional[dict]:
    pos = db_get_position(condition_id)
    if not pos:
        return None

    pnl   = calc_pnl(pos)
    alert = None

    if pnl["should_alert_profit"]:
        alert = {"type": "PROFIT", **pnl, **pos}
        db_mark_alerted(condition_id, "PROFIT")
    elif pnl["should_alert_stop"]:
        alert = {"type": "STOP", **pnl, **pos}
        db_mark_alerted(condition_id, "STOP")

    return alert


def format_positions_message(client=None) -> str:
    positions = db_get_all_positions("open")
    if not positions:
        return "📭 No tienes posiciones abiertas.\nUsa /add para registrar una apuesta."

    lines = ["📊 <b>Tus posiciones abiertas</b>\n"]
    total_invested = 0.0
    total_pnl      = 0.0

    for pos in positions:
        pnl        = calc_pnl(pos)
        emoji      = "🟢" if pnl["pnl_usd"] >= 0 else "🔴"
        act_emoji  = "✅" if pos["action"] == "YES" else "❌"

        days_ago = ""
        try:
            entry_dt = datetime.fromisoformat(pos["created_at"])
            days = (datetime.now(timezone.utc) - entry_dt).days
            days_ago = f"  ({days}d)"
        except Exception:
            pass

        lines.append(
            f"{'─'*35}\n"
            f"{act_emoji} <b>{pos['question'][:60]}</b>\n"
            f"Entrada: {pos['entry_price']:.0%}  →  Ahora: {pos.get('current_price', pos['entry_price']):.0%}{days_ago}\n"
            f"{emoji} P&L: <b>{pnl['pnl_usd']:+.2f}$</b>  ({pnl['pct_change']:+.0%})\n"
            f"Invertido: ${pos['amount_usd']:.2f}  |  Valor: ${pnl['current_value']:.2f}\n"
        )
        total_invested += pos["amount_usd"]
        total_pnl      += pnl["pnl_usd"]

    total_emoji = "🟢" if total_pnl >= 0 else "🔴"
    lines.append(
        f"{'─'*35}\n"
        f"{total_emoji} <b>Total: ${total_invested:.2f} invertido  |  P&L: {total_pnl:+.2f}$</b>"
    )
    return "\n".join(lines)
