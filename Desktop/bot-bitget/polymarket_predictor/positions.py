"""
Gestor de posiciones abiertas.

Guarda las apuestas activas y calcula P&L en tiempo real.
Archivo: positions.json
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

POSITIONS_FILE = Path("positions.json")

# Umbrales por defecto
DEFAULT_PROFIT_TARGET = 0.20   # alerta si ganas +20%
DEFAULT_STOP_LOSS     = -0.25  # alerta si pierdes -25%


def _load() -> dict:
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save(positions: dict):
    POSITIONS_FILE.write_text(json.dumps(positions, indent=2, ensure_ascii=False))


def add_position(condition_id: str, question: str, action: str,
                 entry_price: float, amount_usd: float,
                 profit_target: float = DEFAULT_PROFIT_TARGET,
                 stop_loss: float = DEFAULT_STOP_LOSS) -> dict:
    """
    Registra una posición abierta.
    action: 'YES' o 'NO'
    entry_price: precio al que compraste (0-1)
    amount_usd: dólares invertidos
    """
    positions = _load()
    shares = amount_usd / entry_price if entry_price > 0 else 0
    pos = {
        "condition_id": condition_id,
        "question":     question[:100],
        "action":       action.upper(),
        "entry_price":  round(entry_price, 4),
        "current_price": round(entry_price, 4),
        "amount_usd":   round(amount_usd, 2),
        "shares":       round(shares, 4),
        "profit_target": profit_target,
        "stop_loss":    stop_loss,
        "entry_date":   datetime.now(timezone.utc).isoformat(),
        "alerted_profit": False,
        "alerted_stop":   False,
    }
    positions[condition_id] = pos
    _save(positions)
    logger.info(f"Posición añadida: {condition_id} {action} @ {entry_price:.2%} ${amount_usd}")
    return pos


def remove_position(condition_id: str) -> bool:
    positions = _load()
    if condition_id in positions:
        del positions[condition_id]
        _save(positions)
        return True
    return False


def get_all() -> dict:
    return _load()


def update_price(condition_id: str, current_price: float) -> Optional[dict]:
    """Actualiza el precio actual de una posición. Retorna la posición."""
    positions = _load()
    if condition_id not in positions:
        return None
    positions[condition_id]["current_price"] = round(current_price, 4)
    _save(positions)
    return positions[condition_id]


def calc_pnl(pos: dict) -> dict:
    """
    Calcula P&L de una posición.
    Para YES: ganancia = (current - entry) * shares
    Para NO:  ganancia = (entry_no - current_no) * shares
              donde entry_no = 1-entry_price, current_no = 1-current_price
    """
    entry   = pos["entry_price"]
    current = pos["current_price"]
    shares  = pos["shares"]
    action  = pos["action"]

    if action == "YES":
        pnl_usd    = (current - entry) * shares
        pct_change = (current - entry) / entry if entry > 0 else 0
        current_value = current * shares
    else:  # NO
        entry_no   = 1 - entry
        current_no = 1 - current
        pnl_usd    = (entry_no - current_no) * shares
        pct_change = (entry_no - current_no) / entry_no if entry_no > 0 else 0
        current_value = current_no * shares

    return {
        "pnl_usd":     round(pnl_usd, 2),
        "pct_change":  round(pct_change, 4),
        "current_value": round(current_value, 2),
        "should_alert_profit": (
            pct_change >= pos["profit_target"] and not pos.get("alerted_profit")
        ),
        "should_alert_stop": (
            pct_change <= pos["stop_loss"] and not pos.get("alerted_stop")
        ),
    }


def check_alerts(condition_id: str) -> Optional[dict]:
    """
    Devuelve info de alerta si se superó el target o el stop.
    Marca la alerta como enviada para no repetirla.
    """
    positions = _load()
    pos = positions.get(condition_id)
    if not pos:
        return None

    pnl = calc_pnl(pos)
    alert = None

    if pnl["should_alert_profit"]:
        alert = {"type": "PROFIT", **pnl, **pos}
        positions[condition_id]["alerted_profit"] = True
        _save(positions)
    elif pnl["should_alert_stop"]:
        alert = {"type": "STOP", **pnl, **pos}
        positions[condition_id]["alerted_stop"] = True
        _save(positions)

    return alert


def format_positions_message(client=None) -> str:
    """Genera mensaje Telegram con todas las posiciones y su P&L."""
    positions = _load()
    if not positions:
        return "📭 No tienes posiciones abiertas.\nUsa /add para registrar una apuesta."

    lines = ["📊 <b>Tus posiciones abiertas</b>\n"]
    total_invested = 0
    total_pnl = 0

    for cid, pos in positions.items():
        pnl = calc_pnl(pos)
        emoji = "🟢" if pnl["pnl_usd"] >= 0 else "🔴"
        action_emoji = "✅" if pos["action"] == "YES" else "❌"
        days_ago = ""
        try:
            entry_dt = datetime.fromisoformat(pos["entry_date"])
            days = (datetime.now(timezone.utc) - entry_dt).days
            days_ago = f"  ({days}d)"
        except Exception:
            pass

        lines.append(
            f"{'─'*35}\n"
            f"{action_emoji} <b>{pos['question'][:60]}</b>\n"
            f"Entrada: {pos['entry_price']:.0%}  →  Ahora: {pos['current_price']:.0%}{days_ago}\n"
            f"{emoji} P&L: <b>{pnl['pnl_usd']:+.2f}$</b>  ({pnl['pct_change']:+.0%})\n"
            f"Invertido: ${pos['amount_usd']:.2f}  |  Valor actual: ${pnl['current_value']:.2f}\n"
        )
        total_invested += pos["amount_usd"]
        total_pnl += pnl["pnl_usd"]

    total_emoji = "🟢" if total_pnl >= 0 else "🔴"
    lines.append(
        f"{'─'*35}\n"
        f"{total_emoji} <b>Total invertido: ${total_invested:.2f}  |  P&L: {total_pnl:+.2f}$</b>"
    )
    return "\n".join(lines)
