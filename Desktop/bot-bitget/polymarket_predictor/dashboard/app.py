"""
Dashboard web para PolyiClaude — Nivel 2.
Ahora lee directamente de SQLite para datos en tiempo real.

Nuevas rutas:
  /api/history      → historial completo de oportunidades
  /api/backtest     → resultados del último backtest
  /api/scan_history → historial de escaneos
"""

import os
import json
import time
from pathlib import Path
from datetime import datetime, timezone
from functools import wraps

from flask import Flask, render_template, jsonify, request, Response
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "polymarket2026")
OPPORTUNITIES_FILE = Path("last_opportunities.json")
BACKTEST_FILE      = Path("data/backtest_results.json")

_state = {
    "last_scan":    None,
    "scan_count":   0,
    "opportunities": [],
    "is_scanning":  False,
}


# ── Auth ─────────────────────────────────────────────────────────────

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


# ── Helper: intenta leer de SQLite, cae a JSON si no hay DB ──────────

def _get_db():
    try:
        from database.db import get_db, init_db
        init_db()
        return get_db()
    except Exception:
        return None


def _calc_pnl(pos: dict) -> dict:
    entry   = pos.get("entry_price", 0.5)
    current = pos.get("current_price", entry)
    shares  = pos.get("shares", 0)
    action  = pos.get("action", "YES")

    if action == "YES":
        pnl_usd = (current - entry) * shares
        pct     = (current - entry) / entry if entry > 0 else 0
    else:
        entry_no   = 1 - entry
        current_no = 1 - current
        pnl_usd = (entry_no - current_no) * shares
        pct     = (entry_no - current_no) / entry_no if entry_no > 0 else 0

    return {"pnl_usd": round(pnl_usd, 2), "pct": round(pct * 100, 1)}


# ── Rutas ─────────────────────────────────────────────────────────────

@app.route("/")
@require_auth
def index():
    return render_template("index.html")


@app.route("/api/positions")
@require_auth
def api_positions():
    db = _get_db()
    result = []
    total_invested = total_pnl = 0

    if db:
        rows = db.execute(
            "SELECT * FROM positions WHERE status='open' ORDER BY created_at DESC"
        ).fetchall()
        positions = [dict(r) for r in rows]
    else:
        positions = []

    for pos in positions:
        pnl = _calc_pnl(pos)
        try:
            days_ago = (datetime.now(timezone.utc) -
                        datetime.fromisoformat(pos.get("created_at", ""))).days
        except Exception:
            days_ago = 0

        result.append({
            "id":       pos["condition_id"][:20] + "...",
            "full_id":  pos["condition_id"],
            "question": pos.get("question", "?")[:80],
            "action":   pos.get("action", "YES"),
            "entry":    round(pos.get("entry_price", 0) * 100, 1),
            "current":  round(pos.get("current_price", pos.get("entry_price", 0)) * 100, 1),
            "amount":   pos.get("amount_usd", 0),
            "pnl_usd":  pnl["pnl_usd"],
            "pct":      pnl["pct"],
            "days_ago": days_ago,
            "category": pos.get("category", "—"),
        })
        total_invested += pos.get("amount_usd", 0)
        total_pnl      += pnl["pnl_usd"]

    return jsonify({
        "positions":     result,
        "total_invested": round(total_invested, 2),
        "total_pnl":     round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl / total_invested * 100, 1) if total_invested > 0 else 0,
    })


@app.route("/api/opportunities")
@require_auth
def api_opportunities():
    # Intenta de SQLite primero, fallback a JSON
    db   = _get_db()
    opps = []
    if db:
        rows = db.execute("""
            SELECT * FROM opportunities
            ORDER BY detected_at DESC LIMIT 30
        """).fetchall()
        opps = [dict(r) for r in rows]
    else:
        if OPPORTUNITIES_FILE.exists():
            try:
                opps = json.loads(OPPORTUNITIES_FILE.read_text())
            except Exception:
                pass

    # Normaliza campos para compatibilidad con el frontend
    normalized = []
    for o in opps[:20]:
        normalized.append({
            "question":     o.get("question", ""),
            "market_price": o.get("market_price", 0),
            "probability":  o.get("estimated_prob") or o.get("probability", 0),
            "edge":         o.get("edge", 0),
            "confidence":   o.get("confidence", 0),
            "action":       o.get("action", ""),
            "closes_in":    o.get("closes_in") or o.get("closes_at", "—"),
            "condition_id": o.get("condition_id", ""),
            "category":     o.get("category", "default"),
            "ml_prob":      o.get("ml_prob"),
            "score":        o.get("score"),
        })

    return jsonify({
        "opportunities": normalized,
        "last_scan":     _state["last_scan"],
        "scan_count":    _state["scan_count"],
    })


@app.route("/api/stats")
@require_auth
def api_stats():
    db = _get_db()

    if db:
        try:
            from database.db import db_get_portfolio_summary
            summary = db_get_portfolio_summary()
            return jsonify({
                **summary,
                "is_scanning": _state["is_scanning"],
                "last_scan":   _state["last_scan"] or summary.get("last_scan"),
            })
        except Exception:
            pass

    # Fallback sin DB
    return jsonify({
        "positions_open":   0,
        "total_pnl":        0,
        "total_pnl_pct":    0,
        "total_invested":   0,
        "win_rate":         0,
        "markets_tracked":  0,
        "scan_count":       _state["scan_count"],
        "last_scan":        _state["last_scan"],
        "is_scanning":      _state["is_scanning"],
    })


@app.route("/api/history")
@require_auth
def api_history():
    """Historial de oportunidades detectadas (últimas 7 días)."""
    db = _get_db()
    if not db:
        return jsonify({"history": [], "total": 0})

    rows = db.execute("""
        SELECT category,
               COUNT(*)          AS n,
               AVG(ABS(edge))    AS avg_edge,
               MAX(ABS(edge))    AS max_edge,
               AVG(confidence)   AS avg_conf,
               MAX(detected_at)  AS last_seen
        FROM opportunities
        WHERE detected_at >= datetime('now', '-7 days')
        GROUP BY category
        ORDER BY n DESC
    """).fetchall()

    recent = db.execute("""
        SELECT question, category, edge, confidence, market_price, detected_at
        FROM opportunities
        ORDER BY detected_at DESC LIMIT 50
    """).fetchall()

    return jsonify({
        "by_category": [dict(r) for r in rows],
        "recent":      [dict(r) for r in recent],
    })


@app.route("/api/scan_history")
@require_auth
def api_scan_history():
    """Historial de los últimos 20 escaneos."""
    db = _get_db()
    if not db:
        return jsonify({"scans": []})

    rows = db.execute("""
        SELECT * FROM scan_history ORDER BY scanned_at DESC LIMIT 20
    """).fetchall()
    return jsonify({"scans": [dict(r) for r in rows]})


@app.route("/api/backtest")
@require_auth
def api_backtest():
    """Resultados del último backtest."""
    if BACKTEST_FILE.exists():
        try:
            return jsonify(json.loads(BACKTEST_FILE.read_text()))
        except Exception:
            pass
    return jsonify({"error": "Sin resultados. Usa /backtest en Telegram."})


@app.route("/api/scan", methods=["POST"])
@require_auth
def api_scan():
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
    _state["uptime"] = datetime.now(timezone.utc).isoformat()
    app.run(host=host, port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    run_dashboard()
