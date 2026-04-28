"""
Notificaciones Telegram + loop de monitoreo automático.

Uso:
  1. Primero obtén tu chat_id:   py telegram_bot.py --setup
  2. Luego lanza el monitor:     py telegram_bot.py --monitor
"""

import os
import ssl
import json
import time
import logging
import argparse
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PROXY   = os.getenv("TELEGRAM_PROXY", "")   # ej: socks5://127.0.0.1:1080

SEEN_FILE    = Path("notified_markets.json")   # mercados ya notificados
SCAN_INTERVAL = 15 * 60                         # cada 15 minutos
MAX_ALERTS    = 10                              # máximo mensajes por escaneo

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── Telegram helpers ────────────────────────────────────────────────

class _TLS12Adapter(HTTPAdapter):
    """Fuerza TLS 1.2 — fix para WinError 10054 en Python 3.14/Windows."""
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _new_tg_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _TLS12Adapter())
    session.trust_env = False   # ignora proxies del sistema Windows
    session.verify = False
    if PROXY:
        session.proxies = {"https": PROXY, "http": PROXY}
    return session


def _tg(method: str, req_timeout: int = 15, _retries: int = 3, **kwargs) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    last_err = None
    for attempt in range(_retries):
        try:
            with _new_tg_session() as s:
                r = s.post(url, json=kwargs, timeout=req_timeout)
                return r.json()
        except Exception as e:
            last_err = e
            if attempt < _retries - 1:
                time.sleep(2 ** attempt)
    logger.warning(f"Telegram {method} falló tras {_retries} intentos: {last_err}")
    return {}


def send_message(chat_id: str, text: str) -> bool:
    res = _tg("sendMessage", chat_id=chat_id, text=text,
               parse_mode="HTML", disable_web_page_preview=True)
    return res.get("ok", False)


def get_updates(offset: int = 0) -> list:
    # long polling: timeout=20 en Telegram, req_timeout mayor para no cortar
    res = _tg("getUpdates", req_timeout=30, _retries=1, offset=offset, timeout=20)
    return res.get("result", [])


# ── Setup: detectar chat_id automáticamente ─────────────────────────

def setup():
    """Espera a que el usuario envíe un mensaje al bot y guarda el chat_id."""
    if not TOKEN:
        print("ERROR: TELEGRAM_TOKEN no configurado en .env")
        return

    print("\n" + "="*50)
    print("SETUP — Detección de Chat ID")
    print("="*50)
    print(f"\n1. Abre Telegram y busca tu bot: @PolyiClaude_bot")
    print("2. Envíale cualquier mensaje (ej: 'hola')")
    print("3. Esperando...\n")

    offset = 0
    while True:
        updates = get_updates(offset)
        for upd in updates:
            offset = upd["update_id"] + 1
            msg = upd.get("message", {})
            chat = msg.get("chat", {})
            chat_id = str(chat.get("id", ""))
            username = chat.get("username", chat.get("first_name", "?"))

            if chat_id:
                print(f"✓ Chat ID detectado: {chat_id}  (usuario: {username})")

                # Guarda en .env
                env_path = Path(".env")
                content = env_path.read_text()
                if "TELEGRAM_CHAT_ID=" in content:
                    lines = content.splitlines()
                    lines = [f"TELEGRAM_CHAT_ID={chat_id}" if l.startswith("TELEGRAM_CHAT_ID=")
                             else l for l in lines]
                    env_path.write_text("\n".join(lines) + "\n")
                else:
                    env_path.write_text(content + f"\nTELEGRAM_CHAT_ID={chat_id}\n")

                # Mensaje de bienvenida
                send_message(chat_id, (
                    "✅ <b>PolyiClaude Bot activado</b>\n\n"
                    "Te avisaré cuando encuentre oportunidades en Polymarket.\n"
                    "Lanza el monitor con: <code>py telegram_bot.py --monitor</code>"
                ))
                print("\n✓ Chat ID guardado en .env")
                print("  Ahora ejecuta: py telegram_bot.py --monitor")
                return

        time.sleep(2)


# ── Formateo de alertas ──────────────────────────────────────────────

def _tiempo_restante(days_left):
    if days_left is None:
        return "Sin fecha"
    d = days_left
    if d < 1:
        h = d * 24
        return f"⏰ {h:.0f}h" if h > 1 else "⏰ <1h"
    elif d < 2:
        return f"🔴 {d*24:.0f}h"
    elif d < 7:
        return f"🟡 {d:.1f} días"
    elif d < 30:
        return f"🟢 {d/7:.1f} semanas"
    else:
        return f"🟢 {d/30:.1f} meses"


def _format_alert(analysis, rank: int) -> str:
    p    = analysis.prediction
    pos  = analysis.position
    t    = _tiempo_restante(analysis.days_left)
    cierre = analysis.end_date[:10] if analysis.end_date else "?"

    accion = "BUY YES" if p.edge > 0 else "BUY NO"
    precio_entrada = p.market_price if p.edge > 0 else 1 - p.market_price
    ganancia_10 = round(10 / precio_entrada - 10, 2)
    edge_stars = "⭐" * min(5, max(1, int(abs(p.edge) * 20)))

    # Señales externas (Kalshi / Manifold)
    ext = getattr(analysis, "external", {})
    ext_line = ""
    kal  = ext.get("kalshi")
    mani = ext.get("manifold")
    if kal and kal.get("prob") is not None:
        ext_line = (f"\n🏛 Kalshi: <b>{kal['prob']:.0%}</b>"
                    f"  (similitud {kal['similarity']:.0%})")
    if mani and mani.get("prob") is not None:
        ext_line += (f"\n🔀 Manifold: <b>{mani['prob']:.0%}</b>"
                     f"  (similitud {mani['similarity']:.0%})")

    news = ext.get("news", {})
    news_line = ""
    if news.get("count", 0) > 0:
        s = news["score"]
        emoji = "📰+" if s > 0.1 else ("📰-" if s < -0.1 else "📰")
        news_line = f"\n{emoji} {news['count']} noticia(s) reciente(s)"

    return (
        f"{'━'*38}\n"
        f"<b>#{rank} {analysis.question[:80]}</b>\n"
        f"📅 Cierra: {cierre}  {t}\n"
        f"\n💲 Mercado: <b>{p.market_price:.0%}</b>  →  Mi estimación: <b>{p.probability:.0%}</b>"
        f"{ext_line}{news_line}\n"
        f"📈 Edge: <b>{p.edge:+.0%}</b>  Confianza: <b>{p.confidence:.0%}</b>  {edge_stars}\n"
        f"🎯 Acción: <b>{accion} @ {precio_entrada:.2f}</b>\n"
        f"💵 Con $10 ganarías: <b>${ganancia_10:.2f}</b>  (ROI: {analysis.ev['roi']:.0%})\n"
    )


# ── Tracking de mercados ya notificados ─────────────────────────────

def _load_seen() -> dict:
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_seen(seen: dict):
    SEEN_FILE.write_text(json.dumps(seen, indent=2))


def _is_new_opportunity(condition_id: str, edge: float, seen: dict) -> bool:
    """
    Notifica si:
    - Es un mercado nuevo (nunca visto), o
    - El edge cambió más de 3 puntos respecto a la última notificación
    """
    if condition_id not in seen:
        return True
    prev_edge = seen[condition_id].get("edge", 0)
    return abs(edge - prev_edge) >= 0.03


# ── Loop de monitoreo ────────────────────────────────────────────────

TOPICS = {
    "elon":        ["elon", "musk", "tesla", "spacex", "doge", "x.com"],
    "geopolitica": ["ukraine", "russia", "ceasefire", "war", "nato", "china",
                    "taiwan", "iran", "israel", "gaza", "trump", "sanctions"],
    "crypto":      ["bitcoin", "ethereum", "btc", "eth", "crypto", "solana"],
    "deportes":    ["nba", "nfl", "nhl", "stanley cup", "super bowl", "championship"],
}


def monitor(bankroll: float, limit: int, market_type: str, interval: int,
            min_edge: float, topics: list[str] = None):
    if not TOKEN or not CHAT_ID:
        print("ERROR: Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env")
        print("Ejecuta primero: py telegram_bot.py --setup")
        return

    # Import aquí para no requerir todo el stack si sólo se hace setup
    from predictor import PolymarketPredictor
    from config import EDGE_THRESHOLD

    predictor = PolymarketPredictor(bankroll=bankroll)
    seen      = _load_seen()

    # Construye keywords combinadas de todos los topics seleccionados
    kw = None
    if topics:
        kw = []
        for t in topics:
            kw.extend(TOPICS.get(t, []))

    topics_str = ", ".join(topics) if topics else "todos"
    send_message(CHAT_ID, (
        f"🚀 <b>Monitor iniciado</b>\n"
        f"Temas: {topics_str} | Cada {interval//60} min | "
        f"Edge mínimo: {min_edge:.0%} | Bankroll: ${bankroll:.0f}"
    ))

    logger.info(f"Monitor activo — intervalo {interval}s, topics: {topics_str}")

    scan_count = 0
    while True:
        try:
            scan_count += 1
            logger.info(f"Escaneo #{scan_count}…")

            # Alterna entre mercados normales y eventos con sub-mercados
            if scan_count % 2 == 0:
                raw_markets = predictor.client.get_all_events_markets(
                    keywords=kw, max_pages=30)
                logger.info(f"Via eventos: {len(raw_markets)} mercados")
            else:
                raw_markets = predictor.client.get_all_markets(
                    keywords=kw, max_pages=20)
                logger.info(f"Via mercados: {len(raw_markets)} mercados")

            # Analiza solo los que tienen suficiente liquidez
            results = []
            for mkt in raw_markets:
                try:
                    a = predictor._analyze_market(mkt, market_type)
                    if a:
                        results.append(a)
                except Exception:
                    pass

            results.sort(key=lambda a: a.score, reverse=True)

            opportunities = [
                m for m in results
                if abs(m.prediction.edge) >= min_edge
                and m.prediction.confidence >= 0.4
            ]

            nuevas = [
                (i+1, m) for i, m in enumerate(opportunities)
                if _is_new_opportunity(m.condition_id, m.prediction.edge, seen)
            ]

            if nuevas:
                now_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
                header = (
                    f"🔍 <b>{len(nuevas)} apuesta(s) destacada(s)</b>  •  {now_str}\n"
                    f"ordenadas por edge × confianza × urgencia\n\n"
                )  # reemplaza el bloque original
                bloques = [header]
                for rank, analysis in nuevas[:MAX_ALERTS]:
                    bloques.append(_format_alert(analysis, rank))
                    seen[analysis.condition_id] = {
                        "edge":  round(analysis.prediction.edge, 4),
                        "notified_at": datetime.now(timezone.utc).isoformat(),
                    }

                full_msg = "\n".join(bloques)
                if len(full_msg) > 4000:
                    for rank, analysis in nuevas[:MAX_ALERTS]:
                        send_message(CHAT_ID, _format_alert(analysis, rank))
                else:
                    send_message(CHAT_ID, full_msg)

                _save_seen(seen)
                logger.info(f"Enviadas {len(nuevas)} alertas")
            else:
                logger.info(f"Sin nuevas oportunidades ({len(opportunities)} analizadas, escaneo #{scan_count})")

        except Exception as e:
            logger.error(f"Error en escaneo #{scan_count}: {e}")

        time.sleep(interval)


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyiClaude Telegram Bot")
    parser.add_argument("--setup",    action="store_true",
                        help="Detecta tu chat_id automáticamente")
    parser.add_argument("--monitor",  action="store_true",
                        help="Lanza el monitor continuo")
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument("--limit",    type=int,   default=100)
    parser.add_argument("--type",     default="default",
                        choices=["politics", "sports", "crypto", "economics", "default"])
    parser.add_argument("--interval", type=int,   default=SCAN_INTERVAL,
                        help="Segundos entre escaneos (default: 900 = 15 min)")
    parser.add_argument("--min-edge", type=float, default=0.04,
                        help="Edge mínimo para notificar (default: 0.04 = 4%%)")
    parser.add_argument("--topics",   default=None,
                        help="Temas separados por coma: elon,geopolitica,crypto,deportes")
    args = parser.parse_args()

    if args.setup:
        setup()
    elif args.monitor:
        topics = [t.strip() for t in args.topics.split(",")] if args.topics else None
        monitor(
            bankroll    = args.bankroll,
            limit       = args.limit,
            market_type = args.type,
            interval    = args.interval,
            min_edge    = args.min_edge,
            topics      = topics,
        )
    else:
        parser.print_help()
