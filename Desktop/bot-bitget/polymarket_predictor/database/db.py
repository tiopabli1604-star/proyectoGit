"""
Base de datos SQLite para PolyiClaude.

Reemplaza todos los archivos JSON (positions.json, last_opportunities.json,
notified_markets.json) por una base de datos relacional real.

Ventajas sobre JSON:
  - Consultas complejas (historial, filtros, agregaciones)
  - Sin corrupción por escritura simultánea (WAL mode)
  - Historial completo de oportunidades y escaneos
  - Métricas de performance calculadas por SQL
  - Fácil de inspeccionar con cualquier cliente SQLite
"""

import sqlite3
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/polymarket.db")
_local  = threading.local()   # conexión por hilo


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- ── Posiciones abiertas / cerradas ────────────────────────────────
CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id  TEXT    UNIQUE NOT NULL,
    question      TEXT    NOT NULL,
    action        TEXT    NOT NULL,          -- 'YES' o 'NO'
    entry_price   REAL    NOT NULL,
    current_price REAL,
    amount_usd    REAL    NOT NULL,
    shares        REAL    NOT NULL,
    profit_target REAL    DEFAULT 0.20,
    stop_loss     REAL    DEFAULT -0.25,
    status        TEXT    DEFAULT 'open',    -- 'open' | 'closed' | 'stop' | 'profit'
    alerted_profit INTEGER DEFAULT 0,
    alerted_stop   INTEGER DEFAULT 0,
    pnl_usd       REAL    DEFAULT 0.0,
    pnl_pct       REAL    DEFAULT 0.0,
    created_at    TEXT    NOT NULL,
    updated_at    TEXT    NOT NULL,
    closed_at     TEXT,
    close_price   REAL,
    notes         TEXT
);

-- ── Historial de oportunidades detectadas ─────────────────────────
CREATE TABLE IF NOT EXISTS opportunities (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id  TEXT    NOT NULL,
    question      TEXT    NOT NULL,
    category      TEXT    DEFAULT 'default',
    market_price  REAL,
    estimated_prob REAL,
    edge          REAL,
    confidence    REAL,
    action        TEXT,
    score         REAL,
    ml_prob       REAL,
    cross_prob    REAL,
    news_score    REAL,
    closes_at     TEXT,
    detected_at   TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opp_detected ON opportunities(detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_opp_cid      ON opportunities(condition_id);

-- ── Historial de apuestas seguras ─────────────────────────────────
CREATE TABLE IF NOT EXISTS sure_bets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id  TEXT    NOT NULL,
    question      TEXT    NOT NULL,
    price         REAL,
    side          TEXT,
    roi           REAL,
    days_left     REAL,
    volume        REAL,
    liquidity     REAL,
    detected_at   TEXT    NOT NULL
);

-- ── Historial de escaneos ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS scan_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_number         INTEGER,
    markets_fetched     INTEGER DEFAULT 0,
    markets_analyzed    INTEGER DEFAULT 0,
    opportunities_found INTEGER DEFAULT 0,
    sure_bets_found     INTEGER DEFAULT 0,
    scan_type           TEXT    DEFAULT 'markets',
    duration_seconds    REAL,
    scanned_at          TEXT    NOT NULL
);

-- ── Caché de mercados (evita re-fetch de metadatos) ───────────────
CREATE TABLE IF NOT EXISTS market_cache (
    condition_id  TEXT    PRIMARY KEY,
    question      TEXT,
    market_price  REAL,
    volume_24h    REAL,
    liquidity     REAL,
    spread        REAL,
    end_date      TEXT,
    category      TEXT,
    last_seen     TEXT    NOT NULL
);

-- ── Métricas diarias de performance ───────────────────────────────
CREATE TABLE IF NOT EXISTS daily_metrics (
    date              TEXT    PRIMARY KEY,
    positions_open    INTEGER DEFAULT 0,
    positions_closed  INTEGER DEFAULT 0,
    total_pnl         REAL    DEFAULT 0.0,
    win_count         INTEGER DEFAULT 0,
    loss_count        INTEGER DEFAULT 0,
    total_invested    REAL    DEFAULT 0.0,
    opportunities_found INTEGER DEFAULT 0
);

-- ── Mercados ya notificados (reemplaza notified_markets.json) ─────
CREATE TABLE IF NOT EXISTS notified_markets (
    condition_id  TEXT    PRIMARY KEY,
    edge          REAL,
    notified_at   TEXT    NOT NULL
);
"""


def init_db() -> None:
    """Crea la base de datos y las tablas si no existen."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    logger.info(f"Base de datos inicializada: {DB_PATH}")


def _get_conn() -> sqlite3.Connection:
    """Retorna una conexión SQLite por hilo (thread-local)."""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def get_db() -> sqlite3.Connection:
    return _get_conn()


@contextmanager
def transaction():
    """Context manager para transacciones atómicas."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Posiciones ────────────────────────────────────────────────────────

def db_add_position(condition_id: str, question: str, action: str,
                    entry_price: float, amount_usd: float,
                    profit_target: float = 0.20,
                    stop_loss: float = -0.25) -> dict:
    shares = amount_usd / entry_price if entry_price > 0 else 0
    now    = _now()
    with transaction() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO positions
            (condition_id, question, action, entry_price, current_price,
             amount_usd, shares, profit_target, stop_loss,
             status, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,'open',?,?)
        """, (condition_id, question[:120], action.upper(),
              round(entry_price, 4), round(entry_price, 4),
              round(amount_usd, 2), round(shares, 4),
              profit_target, stop_loss, now, now))
    return db_get_position(condition_id)


def db_get_position(condition_id: str) -> Optional[dict]:
    row = get_db().execute(
        "SELECT * FROM positions WHERE condition_id = ?", (condition_id,)
    ).fetchone()
    return dict(row) if row else None


def db_get_all_positions(status: str = "open") -> list[dict]:
    rows = get_db().execute(
        "SELECT * FROM positions WHERE status = ? ORDER BY created_at DESC",
        (status,)
    ).fetchall()
    return [dict(r) for r in rows]


def db_update_price(condition_id: str, current_price: float,
                    pnl_usd: float = 0.0, pnl_pct: float = 0.0) -> None:
    with transaction() as conn:
        conn.execute("""
            UPDATE positions
            SET current_price = ?, pnl_usd = ?, pnl_pct = ?, updated_at = ?
            WHERE condition_id = ?
        """, (round(current_price, 4), round(pnl_usd, 2),
              round(pnl_pct, 4), _now(), condition_id))


def db_close_position(condition_id: str, close_price: float,
                      pnl_usd: float, pnl_pct: float,
                      status: str = "closed") -> None:
    with transaction() as conn:
        conn.execute("""
            UPDATE positions
            SET status = ?, close_price = ?, pnl_usd = ?, pnl_pct = ?,
                closed_at = ?, updated_at = ?
            WHERE condition_id = ?
        """, (status, round(close_price, 4),
              round(pnl_usd, 2), round(pnl_pct, 4),
              _now(), _now(), condition_id))


def db_remove_position(condition_id: str) -> bool:
    with transaction() as conn:
        n = conn.execute(
            "DELETE FROM positions WHERE condition_id = ?", (condition_id,)
        ).rowcount
    return n > 0


def db_mark_alerted(condition_id: str, alert_type: str) -> None:
    field = "alerted_profit" if alert_type == "PROFIT" else "alerted_stop"
    with transaction() as conn:
        conn.execute(
            f"UPDATE positions SET {field} = 1, updated_at = ? WHERE condition_id = ?",
            (_now(), condition_id)
        )


# ── Oportunidades ─────────────────────────────────────────────────────

def db_save_opportunity(opp: dict) -> None:
    with transaction() as conn:
        conn.execute("""
            INSERT INTO opportunities
            (condition_id, question, category, market_price, estimated_prob,
             edge, confidence, action, score, ml_prob, cross_prob,
             news_score, closes_at, detected_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            opp.get("condition_id", ""),
            opp.get("question", "")[:120],
            opp.get("category", "default"),
            opp.get("market_price"),
            opp.get("probability"),
            opp.get("edge"),
            opp.get("confidence"),
            opp.get("action"),
            opp.get("score"),
            opp.get("ml_prob"),
            opp.get("cross_prob"),
            opp.get("news_score"),
            opp.get("closes_at"),
            _now(),
        ))


def db_get_recent_opportunities(limit: int = 20) -> list[dict]:
    rows = get_db().execute("""
        SELECT * FROM opportunities
        ORDER BY detected_at DESC LIMIT ?
    """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def db_get_opportunity_stats() -> dict:
    """Estadísticas agregadas del historial de oportunidades."""
    row = get_db().execute("""
        SELECT
            COUNT(*)                          AS total,
            AVG(ABS(edge))                    AS avg_edge,
            MAX(ABS(edge))                    AS max_edge,
            AVG(confidence)                   AS avg_confidence,
            SUM(CASE WHEN edge > 0 THEN 1 ELSE 0 END) AS bullish_count,
            SUM(CASE WHEN edge < 0 THEN 1 ELSE 0 END) AS bearish_count
        FROM opportunities
        WHERE detected_at >= datetime('now', '-7 days')
    """).fetchone()
    return dict(row) if row else {}


# ── Historial de escaneos ─────────────────────────────────────────────

def db_log_scan(scan_number: int, markets_fetched: int, markets_analyzed: int,
                opportunities_found: int, sure_bets_found: int,
                scan_type: str, duration_seconds: float) -> None:
    with transaction() as conn:
        conn.execute("""
            INSERT INTO scan_history
            (scan_number, markets_fetched, markets_analyzed,
             opportunities_found, sure_bets_found, scan_type,
             duration_seconds, scanned_at)
            VALUES (?,?,?,?,?,?,?,?)
        """, (scan_number, markets_fetched, markets_analyzed,
              opportunities_found, sure_bets_found, scan_type,
              round(duration_seconds, 2), _now()))


def db_get_scan_stats() -> dict:
    row = get_db().execute("""
        SELECT
            COUNT(*)                 AS total_scans,
            SUM(markets_analyzed)    AS total_analyzed,
            SUM(opportunities_found) AS total_opportunities,
            AVG(duration_seconds)    AS avg_duration,
            MAX(scanned_at)          AS last_scan
        FROM scan_history
    """).fetchone()
    return dict(row) if row else {}


# ── Notificados (reemplaza notified_markets.json) ─────────────────────

def db_is_notified(condition_id: str, edge: float) -> bool:
    """Retorna True si ya fue notificado con edge similar."""
    row = get_db().execute(
        "SELECT edge FROM notified_markets WHERE condition_id = ?",
        (condition_id,)
    ).fetchone()
    if row is None:
        return False
    return abs(float(row["edge"]) - edge) < 0.03


def db_mark_notified(condition_id: str, edge: float) -> None:
    with transaction() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO notified_markets (condition_id, edge, notified_at)
            VALUES (?,?,?)
        """, (condition_id, round(edge, 4), _now()))


# ── P&L agregado ──────────────────────────────────────────────────────

def db_get_portfolio_summary() -> dict:
    """Resumen completo del portafolio."""
    open_pos = db_get_all_positions("open")

    total_invested = sum(p["amount_usd"] for p in open_pos)
    total_pnl      = sum(p["pnl_usd"] for p in open_pos)

    closed = get_db().execute("""
        SELECT COUNT(*) as n, SUM(pnl_usd) as pnl,
               SUM(CASE WHEN pnl_usd > 0 THEN 1 ELSE 0 END) as wins
        FROM positions WHERE status != 'open'
    """).fetchone()
    closed = dict(closed) if closed else {}

    total_closed = closed.get("n", 0) or 0
    total_wins   = closed.get("wins", 0) or 0
    win_rate     = round(total_wins / total_closed * 100, 1) if total_closed > 0 else 0.0

    scan_stats = db_get_scan_stats()

    return {
        "positions_open":   len(open_pos),
        "total_pnl":        round(total_pnl, 2),
        "total_pnl_pct":    round(total_pnl / total_invested * 100, 1) if total_invested > 0 else 0.0,
        "total_invested":   round(total_invested, 2),
        "positions_closed": total_closed,
        "win_rate":         win_rate,
        "markets_tracked":  scan_stats.get("total_analyzed") or 0,
        "scan_count":       scan_stats.get("total_scans") or 0,
        "last_scan":        scan_stats.get("last_scan"),
    }
