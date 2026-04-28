"""
Dashboard web para PolyiClaude.
Corre en el puerto 8080. Protegido con contraseña básica.

Rutas:
  /          → dashboard principal
  /api/positions   → JSON con posiciones
  /api/opportunities → JSON con últimas oportunidades
  /api/stats        → estadísticas generales
  /api/scan         → fuerza un escaneo inmediato
"""

import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, jsonify, request, Response
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "polymarket2026")
POSITIONS_FILE     = Path("positions.json")
SEEN_FILE          = Path("notified_markets.json")
OPPORTUNITIES_FILE = Path("last_opportunities.json")

# Estado compartido entre threads
_state = {
    "last_scan":    None,
    "scan_count":   0,
    "opportunities": [],
    "is_scanning":  False,
}


# ── Auth básica ──────────────────────────────────────────────────────

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.password != DASHBOARD_PASSWORD:
            return Response(
                "Acceso denegado", 401,
                {"WWW-Authenticate": 'Basic realm="PolyiClaude"'}
            )
        return f(*args, **kwargs)
    return decorated


# ── Helpers ──────────────────────────────────────────────────────────

def _load_positions() -> dict:
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text())
        except Exception:
            pass
    return {}


def _load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except Exception:
            pass
    return {}


def _load_opportunities() -> list:
    if OPPORTUNITIES_FILE.exists():
        try:
            return json.loads(OPPORTUNITIES_FILE.read_text())
        except Exception:
            pass
    return []


def _calc_pnl(pos: dict) -> dict:
    entry   = pos.get("entry_price", 0.5)
    current = pos.get("current_price", entry)
    shares  = pos.get("shares", 0)
    action  = pos.get("action", "YES")

    if action == "YES":
        pnl_usd   = (current - entry) * shares
        pct       = (current - entry) / entry if entry > 0 else 0
    else:
        entry_no   = 1 - entry
        current_no = 1 - current
        pnl_usd    = (entry_no - current_no) * shares
        pct        = (entry_no - current_no) / entry_no if entry_no > 0 else 0

    return {"pnl_usd": round(pnl_usd, 2), "pct": round(pct * 100, 1)}


# ── Rutas ────────────────────────────────────────────────────────────

@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/api/positions")
@require_auth
def api_positions():
    positions = _load_positions()
    result = []
    total_invested = total_pnl = 0

    for cid, pos in positions.items():
        pnl = _calc_pnl(pos)
        entry_dt = pos.get("entry_date", "")
        try:
            days_ago = (datetime.now(timezone.utc) -
                        datetime.fromisoformat(entry_dt)).days
        except Exception:
            days_ago = 0

        result.append({
            "id":       cid[:20] + "...",
            "full_id":  cid,
            "question": pos.get("question", "?")[:80],
            "action":   pos.get("action", "YES"),
            "entry":    round(pos.get("entry_price", 0) * 100, 1),
            "current":  round(pos.get("current_price", 0) * 100, 1),
            "amount":   pos.get("amount_usd", 0),
            "pnl_usd":  pnl["pnl_usd"],
            "pct":      pnl["pct"],
            "days_ago": days_ago,
        })
        total_invested += pos.get("amount_usd", 0)
        total_pnl      += pnl["pnl_usd"]

    return jsonify({
        "positions":       result,
        "total_invested":  round(total_invested, 2),
        "total_pnl":       round(total_pnl, 2),
        "total_pnl_pct":   round(total_pnl / total_invested * 100, 1) if total_invested > 0 else 0,
    })


@app.route("/api/opportunities")
@require_auth
def api_opportunities():
    opps = _load_opportunities()
    seen = _load_seen()
    return jsonify({
        "opportunities":  opps[:20],
        "total_tracked":  len(seen),
        "last_scan":      _state["last_scan"],
        "scan_count":     _state["scan_count"],
    })


@app.route("/api/stats")
@require_auth
def api_stats():
    positions = _load_positions()
    seen      = _load_seen()
    pnl_total = sum(_calc_pnl(p)["pnl_usd"] for p in positions.values())
    wins      = sum(1 for p in positions.values() if _calc_pnl(p)["pnl_usd"] > 0)

    return jsonify({
        "positions_open": len(positions),
        "markets_tracked": len(seen),
        "total_pnl":      round(pnl_total, 2),
        "win_rate":       round(wins / len(positions) * 100) if positions else 0,
        "scan_count":     _state["scan_count"],
        "last_scan":      _state["last_scan"],
        "is_scanning":    _state["is_scanning"],
        "uptime":         _state.get("uptime", "—"),
    })


@app.route("/api/scan", methods=["POST"])
@require_auth
def api_scan():
    """Fuerza un escaneo inmediato (no bloquea — responde inmediatamente)."""
    # Escribe un flag que el monitor principal detecta
    Path("force_scan.flag").write_text("1")
    return jsonify({"ok": True, "message": "Escaneo forzado. Resultados en ~30s."})


@app.route("/api/remove_position", methods=["POST"])
@require_auth
def api_remove_position():
    data = request.get_json() or {}
    cid  = data.get("condition_id", "")
    if not cid:
        return jsonify({"ok": False, "error": "condition_id requerido"})
    from positions import remove_position
    ok = remove_position(cid)
    return jsonify({"ok": ok})


def run_dashboard(host="0.0.0.0", port=8080):
    """Inicia el dashboard en un thread separado."""
    _state["uptime"] = datetime.now(timezone.utc).isoformat()
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_dashboard()
