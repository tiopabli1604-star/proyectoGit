"""
Notificaciones Telegram + loop de monitoreo automático.

Comandos disponibles (envía desde Telegram):
  /scan      — escaneo inmediato
  /top       — top 5 oportunidades ahora
  /pos       — ver tus posiciones y P&L
  /add <conditionId> <YES|NO> <precio> <cantidad$>  — añadir posición
  /remove <conditionId>                              — eliminar posición
  /help      — ayuda

Uso CLI:
  py telegram_bot.py --setup    → detecta tu chat_id
  py telegram_bot.py --monitor  → lanza el monitor continuo
"""

import os
import ssl
import json
import time
import logging
import argparse
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import datetime, timezone, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
PROXY   = os.getenv("TELEGRAM_PROXY", "")

SEEN_FILE     = Path("notified_markets.json")
SCAN_INTERVAL = 15 * 60     # 15 minutos
MAX_ALERTS    = 10
DAILY_SUMMARY_HOUR = 9      # resumen a las 9:00 UTC

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── Telegram SSL fix (WinError 10054 + servidor Linux) ───────────────

class _TLS12Adapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        super().init_poolmanager(*args, **kwargs)


def _new_tg_session() -> requests.Session:
    session = requests.Session()
    session.mount("https://", _TLS12Adapter())
    session.trust_env = False
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
    res = _tg("getUpdates", req_timeout=30, _retries=1, offset=offset, timeout=20)
    return res.get("result", [])


# ── Setup ────────────────────────────────────────────────────────────

def setup():
    if not TOKEN:
        print("ERROR: TELEGRAM_TOKEN no configurado en .env")
        return

    print("\n" + "="*50)
    print("SETUP — Detección de Chat ID")
    print("="*50)
    print(f"\n1. Abre Telegram y busca tu bot")
    print("2. Envíale cualquier mensaje (ej: 'hola')")
    print("3. Esperando...\n")

    offset = 0
    while True:
        updates = get_updates(offset)
        for upd in updates:
            offset = upd["update_id"] + 1
            msg  = upd.get("message", {})
            chat = msg.get("chat", {})
            chat_id  = str(chat.get("id", ""))
            username = chat.get("username", chat.get("first_name", "?"))

            if chat_id:
                print(f"✓ Chat ID detectado: {chat_id}  (usuario: {username})")
                env_path = Path(".env")
                content  = env_path.read_text() if env_path.exists() else ""
                if "TELEGRAM_CHAT_ID=" in content:
                    lines = [f"TELEGRAM_CHAT_ID={chat_id}"
                             if l.startswith("TELEGRAM_CHAT_ID=") else l
                             for l in content.splitlines()]
                    env_path.write_text("\n".join(lines) + "\n")
                else:
                    env_path.write_text(content + f"\nTELEGRAM_CHAT_ID={chat_id}\n")

                send_message(chat_id, (
                    "✅ <b>PolyiClaude Bot activado</b>\n\n"
                    "Comandos disponibles:\n"
                    "/scan — escaneo inmediato\n"
                    "/top  — top 5 oportunidades\n"
                    "/pos  — tus posiciones y P&L\n"
                    "/add  — registrar apuesta\n"
                    "/help — ayuda completa"
                ))
                print("\n✓ Chat ID guardado en .env")
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
    p      = analysis.prediction
    t      = _tiempo_restante(analysis.days_left)
    cierre = analysis.end_date[:10] if analysis.end_date else "?"

    accion = "BUY YES" if p.edge > 0 else "BUY NO"
    precio_entrada = p.market_price if p.edge > 0 else 1 - p.market_price
    ganancia_10 = round(10 / precio_entrada - 10, 2) if precio_entrada > 0 else 0
    edge_stars  = "⭐" * min(5, max(1, int(abs(p.edge) * 20)))

    # Señales externas
    ext  = getattr(analysis, "external", {})
    kal  = ext.get("kalshi")
    mani = ext.get("manifold")
    ext_line = ""
    if kal and kal.get("prob") is not None:
        ext_line += f"\n🏛 Kalshi: <b>{kal['prob']:.0%}</b>  (similitud {kal['similarity']:.0%})"
    if mani and mani.get("prob") is not None:
        ext_line += f"\n🔀 Manifold: <b>{mani['prob']:.0%}</b>  (similitud {mani['similarity']:.0%})"

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
        f"\n💲 Mercado: <b>{p.market_price:.0%}</b>  →  Estimación: <b>{p.probability:.0%}</b>"
        f"{ext_line}{news_line}\n"
        f"📈 Edge: <b>{p.edge:+.0%}</b>  Confianza: <b>{p.confidence:.0%}</b>  {edge_stars}\n"
        f"🎯 Acción: <b>{accion} @ {precio_entrada:.2f}</b>\n"
        f"💵 Con $10 ganarías: <b>${ganancia_10:.2f}</b>  (ROI: {analysis.ev['roi']:.0%})\n"
        f"🆔 <code>{analysis.condition_id[:20]}...</code>\n"
    )


# ── Tracking de mercados notificados ─────────────────────────────────

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
    if condition_id not in seen:
        return True
    prev_edge = seen[condition_id].get("edge", 0)
    return abs(edge - prev_edge) >= 0.03


# ── Topics / Keywords ────────────────────────────────────────────────

TOPICS = {
    "politica": [
        "trump", "biden", "harris", "congress", "senate", "house",
        "republican", "democrat", "president", "supreme court",
        "white house", "executive order", "ukraine", "russia",
        "ceasefire", "war", "nato", "china", "taiwan", "iran",
        "israel", "gaza", "sanctions", "tariff", "tariffs",
        "trade war", "xi jinping", "putin", "zelensky",
        "kim jong", "north korea", "middle east", "eu", "europe",
        "parliament", "minister", "government", "coup", "protest",
    ],
    "elecciones": [
        "election", "vote", "voting", "ballot", "polls", "poll",
        "candidate", "primary", "runoff", "referendum", "majority",
        "win the election", "presidential", "congressional",
        "senate race", "gubernatorial", "party", "margin",
        "swing state", "approval rating", "incumbent",
        "germany election", "france election", "uk election",
        "mexico election", "brazil election", "india election",
        "japan election", "korea election", "australia election",
    ],
    "finanzas": [
        "fed", "federal reserve", "interest rate", "rate cut",
        "rate hike", "inflation", "cpi", "gdp", "recession",
        "stock market", "s&p", "sp500", "nasdaq", "dow jones",
        "earnings", "ipo", "bonds", "treasury", "yield",
        "dollar", "euro", "yen", "oil", "gold", "silver",
        "housing", "unemployment", "jobs report", "payroll",
        "hedge fund", "bank", "jpmorgan", "goldman", "blackrock",
        "imf", "world bank", "debt ceiling", "budget deficit",
    ],
    "crypto": [
        "bitcoin", "ethereum", "btc", "eth", "crypto", "solana",
        "xrp", "coinbase", "binance", "sol", "defi", "nft",
        "altcoin", "stablecoin", "sec crypto", "etf bitcoin",
        "ripple", "cardano", "ada", "matic", "polygon", "avalanche",
        "avax", "chainlink", "link", "doge", "dogecoin", "shiba",
        "crypto regulation", "bitcoin etf", "crypto market",
        "blockchain", "web3", "token", "airdrop", "halving",
    ],
}


# ── Correlación entre mercados ───────────────────────────────────────

def _detect_contradictions(opportunities: list) -> set:
    """
    Detecta pares de mercados donde apostar en ambos se contradice.
    Ej: 'A gana el campeonato' y 'B gana el campeonato'.
    Retorna los condition_ids del segundo mercado de cada par contradictorio.
    """
    flagged = set()
    seen_themes = {}

    for analysis in opportunities:
        q = analysis.question.lower()
        words = set(q.split())
        for prev_id, prev_words in seen_themes.items():
            # Si comparten muchas palabras clave pero son mercados distintos
            overlap = len(words & prev_words) / max(len(words | prev_words), 1)
            if overlap > 0.5 and prev_id != analysis.condition_id:
                # Marca el de menor score como contradicción
                flagged.add(analysis.condition_id)
        seen_themes[analysis.condition_id] = words

    return flagged


# ── Apuestas seguras (mercados al 85-97%) ────────────────────────────

def _format_sure_bet(mkt: dict, rank: int) -> str:
    """Formatea una apuesta de alta probabilidad para Telegram."""
    question = mkt["question"][:80]
    price    = mkt["price"]
    side     = mkt["side"]
    roi      = mkt["roi"]
    days     = mkt["days_left"]
    vol      = mkt["volume"]
    liq      = mkt["liquidity"]

    if days is None:
        tiempo = "Sin fecha"
    elif days < 1:
        tiempo = f"⏰ {days*24:.0f}h"
    elif days < 7:
        tiempo = f"🔴 {days:.1f} días"
    elif days < 30:
        tiempo = f"🟡 {days/7:.1f} semanas"
    else:
        tiempo = f"🟢 {days/30:.1f} meses"

    profit_10 = round(10 * roi, 2)

    return (
        f"{'━'*38}\n"
        f"<b>#{rank} {question}</b>\n"
        f"⏳ Cierra en: {tiempo}\n"
        f"\n✅ Probabilidad: <b>{price:.0%}</b>  →  Apuesta: <b>{side}</b>\n"
        f"💰 ROI: <b>+{roi:.1%}</b>  |  Con $10 ganarías: <b>${profit_10:.2f}</b>\n"
        f"📊 Volumen: ${vol:,.0f}  |  Liquidez: ${liq:,.0f}\n"
    )


def _scan_sure_bets(client, keywords: list = None, max_pages: int = 20) -> list[dict]:
    """
    Busca mercados donde el precio del consenso es 85-97%:
    prácticamente seguro pero con ROI decente.
    """
    try:
        markets = client.get_all_markets(keywords=keywords, max_pages=max_pages)
    except Exception as e:
        logger.error(f"Error obteniendo mercados para sure bets: {e}")
        return []

    sure_bets = []
    for mkt in markets:
        try:
            prices = mkt.get("outcomePrices", [])
            if isinstance(prices, str):
                import json as _j
                try:
                    prices = _j.loads(prices)
                except Exception:
                    prices = []
            if not prices:
                continue
            yes_price = float(prices[0])

            # Rango óptimo: 85-97% (suficiente ROI, sin demasiado riesgo residual)
            if 0.85 <= yes_price <= 0.97:
                price, side = yes_price, "BUY YES"
            elif 0.03 <= yes_price <= 0.15:
                price, side = 1 - yes_price, "BUY NO"
            else:
                continue

            volume  = float(mkt.get("volume24hr") or mkt.get("volume24hrClob") or 0)
            liq     = float(mkt.get("liquidity") or mkt.get("liquidityClob") or 0)

            # Solo mercados líquidos
            if liq < 300 and volume < 200:
                continue

            end_date = mkt.get("endDate") or mkt.get("endDateIso") or mkt.get("end_date_iso")
            dl = _days_left(end_date)
            if dl is not None and dl <= 0:
                continue

            # Máximo 90 días (más lejos hay mucho tiempo para sorpresas)
            if dl is not None and dl > 90:
                continue

            roi = (1.0 / price) - 1.0

            # Score: liquidez + ROI + urgencia temporal
            days_factor = 1.0 / (dl + 1) if dl else 0.01
            score = liq * roi * (1 + days_factor)

            sure_bets.append({
                "question":    mkt.get("question", "?"),
                "price":       price,
                "side":        side,
                "roi":         roi,
                "days_left":   dl,
                "volume":      volume,
                "liquidity":   liq,
                "score":       score,
                "condition_id": mkt.get("conditionId") or mkt.get("condition_id", ""),
            })
        except Exception:
            continue

    sure_bets.sort(key=lambda x: x["score"], reverse=True)
    return sure_bets[:10]


# ── Comandos Telegram (bidireccional) ────────────────────────────────

def _handle_command(text: str, predictor, seen: dict, topics_kw: list,
                    market_type: str, min_edge: float) -> str:
    """Procesa un comando enviado por el usuario y retorna la respuesta."""
    from positions import (add_position, remove_position,
                           format_positions_message, get_all)

    cmd_parts = text.strip().split()
    cmd = cmd_parts[0].lower()

    if cmd in ("/help", "/start"):
        return (
            "🤖 <b>PolyiClaude Bot — Comandos</b>\n\n"
            "📊 <b>Análisis</b>\n"
            "/scan — escaneo inmediato (edge alto)\n"
            "/top  — top 5 oportunidades ahora\n"
            "/sure — apuestas seguras 85-97% (bajo riesgo)\n\n"
            "💼 <b>Posiciones</b>\n"
            "/pos  — ver P&amp;L de tus apuestas\n"
            "/add &lt;id&gt; &lt;YES|NO&gt; &lt;precio&gt; &lt;$&gt;\n"
            "/remove &lt;id&gt; — eliminar posición\n\n"
            "💸 <b>Trading automático</b>\n"
            "/buy &lt;id&gt; &lt;YES|NO&gt; &lt;$&gt; — ejecutar orden\n"
            "/balance — ver USDC disponible\n\n"
            "🌐 Dashboard: http://187.33.156.155:8080"
        )

    elif cmd == "/sure":
        try:
            bets = _scan_sure_bets(predictor.client, keywords=topics_kw or None)
            if not bets:
                return "🔍 Sin apuestas seguras disponibles ahora mismo (85-97%)."
            lines = [f"🔒 <b>Apuestas seguras — alta probabilidad</b>\n"
                     f"Mercados al 85-97% con buena liquidez\n"]
            for i, b in enumerate(bets[:6], 1):
                lines.append(_format_sure_bet(b, i))
            lines.append(
                "\n⚠️ <i>Aunque la probabilidad es alta, nunca es 100%.\n"
                "Diversifica y no pongas todo en una sola apuesta.</i>"
            )
            return "\n".join(lines)[:4000]
        except Exception as e:
            return f"❌ Error: {e}"

    elif cmd == "/pos":
        return format_positions_message()

    elif cmd == "/remove":
        if len(cmd_parts) < 2:
            return "Uso: /remove <conditionId>"
        cid = cmd_parts[1]
        if remove_position(cid):
            return f"✅ Posición {cid[:20]}... eliminada."
        return f"❌ No encontré la posición {cid[:20]}..."

    elif cmd == "/add":
        if len(cmd_parts) < 5:
            return (
                "Uso: /add <conditionId> <YES|NO> <precio> <cantidad$>\n"
                "Ej:  /add 0xabc123 YES 0.45 10"
            )
        try:
            cid    = cmd_parts[1]
            action = cmd_parts[2].upper()
            price  = float(cmd_parts[3])
            amount = float(cmd_parts[4])
            if action not in ("YES", "NO"):
                return "❌ La acción debe ser YES o NO"
            if not (0 < price < 1):
                return "❌ El precio debe estar entre 0 y 1 (ej: 0.45)"

            # Busca la pregunta del mercado
            try:
                mkt = predictor.client.get_market(cid)
                question = mkt.get("question", "Mercado desconocido")
            except Exception:
                question = "Mercado desconocido"

            pos = add_position(cid, question, action, price, amount)
            return (
                f"✅ <b>Posición registrada</b>\n\n"
                f"Mercado: {question[:60]}\n"
                f"Acción: {action} @ {price:.0%}\n"
                f"Inversión: ${amount:.2f}\n\n"
                f"Te avisaré cuando suba +20% o baje -25%."
            )
        except (ValueError, IndexError):
            return "❌ Formato incorrecto. Usa: /add <id> <YES|NO> <precio> <cantidad>"

    elif cmd in ("/scan", "/top"):
        top_n = 5 if cmd == "/top" else MAX_ALERTS
        try:
            raw = predictor.client.get_all_markets(
                keywords=topics_kw or None, max_pages=15)
            results = []
            for mkt in raw:
                try:
                    a = predictor._analyze_market(mkt, market_type)
                    if a:
                        results.append(a)
                except Exception:
                    pass
            results.sort(key=lambda a: a.score, reverse=True)
            opps = [m for m in results
                    if abs(m.prediction.edge) >= min_edge
                    and m.prediction.confidence >= 0.4][:top_n]

            if not opps:
                return f"🔍 Sin oportunidades con edge ≥ {min_edge:.0%} ahora mismo."

            bloques = [f"🔍 <b>{len(opps)} oportunidad(es)</b>\n"]
            for i, a in enumerate(opps, 1):
                bloques.append(_format_alert(a, i))
            return "\n".join(bloques)[:4000]
        except Exception as e:
            return f"❌ Error en escaneo: {e}"

    elif cmd == "/buy":
        # /buy <conditionId> <YES|NO> <cantidad$>
        if len(cmd_parts) < 4:
            return (
                "Uso: /buy <conditionId> <YES|NO> <cantidad$>\n"
                "Ej:  /buy 0xabc YES 10\n\n"
                "⚠️ Requiere POLY_PRIVATE_KEY en .env"
            )
        try:
            from trading.executor import is_configured, buy_yes, buy_no, get_balance, MAX_ORDER_USD, MIN_EDGE_TO_BUY
            if not is_configured():
                return ("❌ API de Polymarket no configurada.\n"
                        "Añade POLY_PRIVATE_KEY al .env del servidor.")
            cid    = cmd_parts[1]
            action = cmd_parts[2].upper()
            amount = float(cmd_parts[3])

            if amount > MAX_ORDER_USD:
                return f"❌ Máximo por orden: ${MAX_ORDER_USD:.0f}"

            mkt = predictor.client.get_market(cid)
            question = mkt.get("question", "?")
            tokens = mkt.get("clobTokenIds", [])
            if isinstance(tokens, str):
                import json as _j
                try: tokens = _j.loads(tokens)
                except: tokens = []

            if not tokens:
                return "❌ No se encontraron tokens para este mercado."

            balance = get_balance()
            if balance is not None and balance < amount:
                return f"❌ Balance insuficiente: ${balance:.2f} < ${amount:.2f}"

            if action == "YES":
                result = buy_yes(cid, tokens[0], amount, question)
            else:
                token_no = tokens[1] if len(tokens) > 1 else tokens[0]
                result = buy_no(cid, token_no, amount, question)

            if result["ok"]:
                from positions import add_position
                prices = mkt.get("outcomePrices", [])
                if isinstance(prices, str):
                    import json as _j
                    try: prices = _j.loads(prices)
                    except: prices = []
                entry_price = float(prices[0]) if action == "YES" else (1 - float(prices[0])) if prices else 0.5
                add_position(cid, question, action, entry_price, amount)
                return (
                    f"✅ <b>Orden ejecutada</b>\n\n"
                    f"{question[:60]}\n"
                    f"Acción: {action}  |  Cantidad: ${amount:.2f}\n"
                    f"Filled: {result.get('filled', 0):.2f} contratos\n\n"
                    f"Posición registrada. Usa /pos para ver tu P&L."
                )
            else:
                return f"❌ Error ejecutando orden: {result.get('error', 'desconocido')}"
        except ValueError:
            return "❌ Cantidad inválida. Ej: /buy 0xabc YES 10"
        except Exception as e:
            return f"❌ Error: {e}"

    elif cmd == "/balance":
        try:
            from trading.executor import get_balance, is_configured
            if not is_configured():
                return "❌ API no configurada. Necesitas POLY_PRIVATE_KEY en .env"
            bal = get_balance()
            return f"💰 Balance en Polymarket: <b>${bal:.2f} USDC</b>" if bal is not None else "❌ Error obteniendo balance."
        except Exception as e:
            return f"❌ Error: {e}"

    return f"❓ Comando no reconocido: {cmd}\nEscribe /help para ver los comandos."


# ── Resumen diario ───────────────────────────────────────────────────

def _should_send_daily_summary(last_summary_date: str) -> bool:
    now = datetime.now(timezone.utc)
    if now.hour != DAILY_SUMMARY_HOUR:
        return False
    today = now.strftime("%Y-%m-%d")
    return last_summary_date != today


def _build_daily_summary(seen: dict, best_today: list) -> str:
    from positions import format_positions_message
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    pos_msg = format_positions_message()
    total_tracked = len(seen)

    lines = [
        f"☀️ <b>Resumen diario — {now_str}</b>\n",
        f"📊 Mercados rastreados: {total_tracked}",
    ]
    if best_today:
        lines.append(f"\n🏆 <b>Mejores de las últimas 24h:</b>")
        for i, a in enumerate(best_today[:3], 1):
            lines.append(f"  {i}. {a.question[:60]} — edge {a.prediction.edge:+.0%}")
    lines.append(f"\n{pos_msg[:1500]}")
    return "\n".join(lines)


# ── Monitoreo de posiciones ──────────────────────────────────────────

def _check_position_alerts(predictor) -> list[str]:
    """
    Comprueba si alguna posición llegó al target o al stop loss.
    Retorna lista de mensajes de alerta.
    """
    from positions import get_all, update_price, check_alerts, calc_pnl

    alerts = []
    positions = get_all()
    if not positions:
        return []

    for cid, pos in positions.items():
        try:
            mkt = predictor.client.get_market(cid)
            prices = mkt.get("outcomePrices", [])
            if isinstance(prices, str):
                import json as _json
                try:
                    prices = _json.loads(prices)
                except Exception:
                    prices = []
            if not prices:
                continue
            current_price = float(prices[0])
            update_price(cid, current_price)
            alert = check_alerts(cid)
            if alert:
                emoji = "🚀" if alert["type"] == "PROFIT" else "🛑"
                action_price = (current_price if pos["action"] == "YES"
                                else 1 - current_price)
                msg = (
                    f"{emoji} <b>Alerta de posición</b>\n\n"
                    f"{pos['question'][:70]}\n\n"
                    f"Entrada: {pos['entry_price']:.0%}  →  Ahora: {current_price:.0%}\n"
                    f"P&L: <b>{alert['pnl_usd']:+.2f}$ ({alert['pct_change']:+.0%})</b>\n\n"
                )
                if alert["type"] == "PROFIT":
                    msg += f"✅ <b>¡Target alcanzado! Considera vender @ {action_price:.2f}</b>"
                else:
                    msg += f"⚠️ <b>Stop loss activado. Considera salir @ {action_price:.2f}</b>"
                alerts.append(msg)
        except Exception as e:
            logger.debug(f"Error checking position {cid}: {e}")

    return alerts


# ── Loop de monitoreo principal ──────────────────────────────────────

def monitor(bankroll: float, limit: int, market_type: str, interval: int,
            min_edge: float, topics: list = None):
    if not TOKEN or not CHAT_ID:
        print("ERROR: Falta TELEGRAM_TOKEN o TELEGRAM_CHAT_ID en .env")
        return

    from predictor import PolymarketPredictor

    # Arranca el dashboard web en background
    try:
        from dashboard.app import run_dashboard
        dash_thread = threading.Thread(target=run_dashboard, daemon=True)
        dash_thread.start()
        logger.info("Dashboard web iniciado en :8080")
    except Exception as e:
        logger.warning(f"Dashboard no disponible: {e}")

    predictor = PolymarketPredictor(bankroll=bankroll)
    seen      = _load_seen()

    kw = None
    if topics:
        kw = []
        for t in topics:
            kw.extend(TOPICS.get(t, []))

    topics_str = ", ".join(topics) if topics else "todos"
    send_message(CHAT_ID, (
        f"🚀 <b>Monitor iniciado</b>\n"
        f"Temas: {topics_str} | Cada {interval//60} min | "
        f"Edge mínimo: {min_edge:.0%} | Bankroll: ${bankroll:.0f}\n\n"
        f"Comandos: /scan /top /sure /pos /add /help"
    ))

    logger.info(f"Monitor activo — intervalo {interval}s, topics: {topics_str}")

    scan_count         = 0
    update_offset      = 0
    last_summary       = ""
    best_today: list   = []
    last_cmd_check     = 0
    seen_sure_bets: set = set()   # condition_ids ya notificados como sure bets

    while True:
        # ── Comandos Telegram (cada 30s) ──
        now_ts = time.time()
        if now_ts - last_cmd_check >= 30:
            last_cmd_check = now_ts
            try:
                updates = get_updates(update_offset)
                for upd in updates:
                    update_offset = upd["update_id"] + 1
                    msg  = upd.get("message", {})
                    text = msg.get("text", "").strip()
                    if text.startswith("/"):
                        logger.info(f"Comando recibido: {text}")
                        resp = _handle_command(
                            text, predictor, seen, kw or [],
                            market_type, min_edge)
                        send_message(CHAT_ID, resp)
            except Exception as e:
                logger.debug(f"Error checking commands: {e}")

        # ── Resumen diario ──
        if _should_send_daily_summary(last_summary):
            msg = _build_daily_summary(seen, best_today)
            send_message(CHAT_ID, msg)
            last_summary = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            best_today = []

        # ── Alertas de posiciones ──
        try:
            pos_alerts = _check_position_alerts(predictor)
            for alert_msg in pos_alerts:
                send_message(CHAT_ID, alert_msg)
        except Exception as e:
            logger.debug(f"Error checking positions: {e}")

        # ── Sure bets automáticas (cada 3 escaneos = cada 45 min) ──
        if scan_count % 3 == 0:
            try:
                sure_bets = _scan_sure_bets(predictor.client, keywords=kw)
                nuevas_sure = [b for b in sure_bets
                               if b["condition_id"] not in seen_sure_bets]
                if nuevas_sure:
                    lines = [
                        f"🔒 <b>Apuestas seguras detectadas</b>  •  "
                        f"{datetime.now(timezone.utc).strftime('%H:%M UTC')}\n"
                        f"Mercados al 85-97% — bajo riesgo, volumen para acumular\n"
                    ]
                    for i, b in enumerate(nuevas_sure[:5], 1):
                        lines.append(_format_sure_bet(b, i))
                        seen_sure_bets.add(b["condition_id"])
                    lines.append(
                        "\n⚠️ <i>Alta probabilidad no es certeza. "
                        "Diversifica tus posiciones.</i>"
                    )
                    send_message(CHAT_ID, "\n".join(lines)[:4000])
                    logger.info(f"Enviadas {len(nuevas_sure)} sure bets")
            except Exception as e:
                logger.debug(f"Error en sure bets: {e}")

        # ── Force scan desde dashboard ──
        if Path("force_scan.flag").exists():
            try: Path("force_scan.flag").unlink()
            except Exception: pass
            logger.info("Escaneo forzado desde dashboard")

        # ── Escaneo de mercados (cada interval segundos) ──
        scan_count += 1
        try:
            logger.info(f"Escaneo #{scan_count}…")

            if scan_count % 2 == 0:
                raw_markets = predictor.client.get_all_events_markets(
                    keywords=kw, max_pages=30)
                logger.info(f"Via eventos: {len(raw_markets)} mercados")
            else:
                raw_markets = predictor.client.get_all_markets(
                    keywords=kw, max_pages=20)
                logger.info(f"Via mercados: {len(raw_markets)} mercados")

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

            # Filtra mercados contradictorios entre sí
            contradictions = _detect_contradictions(opportunities)
            opportunities = [m for m in opportunities
                             if m.condition_id not in contradictions]

            nuevas = [
                (i+1, m) for i, m in enumerate(opportunities)
                if _is_new_opportunity(m.condition_id, m.prediction.edge, seen)
            ]

            # Guarda oportunidades para el dashboard
            try:
                opp_data = [{
                    "question":     m.question[:80],
                    "market_price": m.prediction.market_price,
                    "probability":  m.prediction.probability,
                    "edge":         m.prediction.edge,
                    "confidence":   m.prediction.confidence,
                    "action":       m.prediction.recommendation,
                    "closes_in":    _tiempo_restante(m.days_left),
                    "condition_id": m.condition_id,
                } for m in opportunities[:20]]
                Path("last_opportunities.json").write_text(
                    json.dumps(opp_data, ensure_ascii=False)
                )
                from dashboard.app import _state as dash_state
                dash_state["last_scan"]   = datetime.now(timezone.utc).isoformat()
                dash_state["scan_count"]  = scan_count
                dash_state["opportunities"] = opp_data
            except Exception:
                pass

            # Guarda las mejores del día para el resumen
            best_today = sorted(
                best_today + [m for _, m in nuevas],
                key=lambda a: a.score, reverse=True
            )[:10]

            if nuevas:
                now_str = datetime.now(timezone.utc).strftime('%H:%M UTC')
                header  = (
                    f"🔍 <b>{len(nuevas)} apuesta(s) destacada(s)</b>  •  {now_str}\n"
                    f"ordenadas por edge × confianza × urgencia\n\n"
                )
                bloques = [header]
                for rank, analysis in nuevas[:MAX_ALERTS]:
                    bloques.append(_format_alert(analysis, rank))
                    seen[analysis.condition_id] = {
                        "edge":       round(analysis.prediction.edge, 4),
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
                logger.info(
                    f"Sin nuevas oportunidades "
                    f"({len(opportunities)} analizadas, escaneo #{scan_count})"
                )

        except Exception as e:
            logger.error(f"Error en escaneo #{scan_count}: {e}")

        time.sleep(interval)


# ── CLI ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PolyiClaude Telegram Bot")
    parser.add_argument("--setup",    action="store_true")
    parser.add_argument("--monitor",  action="store_true")
    parser.add_argument("--bankroll", type=float, default=1000.0)
    parser.add_argument("--limit",    type=int,   default=100)
    parser.add_argument("--type",     default="default",
                        choices=["politics", "sports", "crypto", "economics", "default"])
    parser.add_argument("--interval", type=int,   default=SCAN_INTERVAL)
    parser.add_argument("--min-edge", type=float, default=0.04)
    parser.add_argument("--topics",   default=None,
                        help="Temas: elon,geopolitica,crypto,deportes,politica_usa")
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
