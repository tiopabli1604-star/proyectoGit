"""
Busca mercados que cierran esta semana o la siguiente con el mejor edge.
"""
import requests
from datetime import datetime, timezone, timedelta

GAMMA = "https://gamma-api.polymarket.com"
CLOB  = "https://clob.polymarket.com"

now      = datetime.now(timezone.utc)
deadline = now + timedelta(days=14)

print(f"Buscando mercados que cierran antes del {deadline.strftime('%Y-%m-%d')}...\n")

# Recoge todos los mercados paginando
all_markets = []
for offset in range(0, 3000, 100):
    try:
        r = requests.get(f"{GAMMA}/markets", params={
            "limit": 100,
            "offset": offset,
            "active": "true",
            "closed": "false",
        }, timeout=10)
        batch = r.json()
        if isinstance(batch, dict):
            batch = batch.get("data", batch.get("markets", []))
        if not batch:
            break
        all_markets.extend(batch)
        if len(batch) < 100:
            break
    except Exception as e:
        print(f"  Error en offset {offset}: {e}")
        break

print(f"Total mercados obtenidos: {len(all_markets)}")

# Filtra por fecha de cierre <= 14 días
upcoming = []
for m in all_markets:
    end_str = m.get("endDate") or m.get("endDateIso") or ""
    if not end_str:
        continue
    try:
        end = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        days_left = (end - now).total_seconds() / 86400
        if 0 < days_left <= 14:
            m["_days_left"] = round(days_left, 1)
            upcoming.append(m)
    except Exception:
        continue

print(f"Con cierre en los próximos 14 días: {len(upcoming)}\n")

if not upcoming:
    print("No se encontraron mercados próximos a cerrar.")
    print("Prueba: py gangas.py (sin filtros adicionales)")
else:
    # Ordena por volumen 24h y liquidez
    upcoming.sort(key=lambda m: float(m.get("volume24hr") or 0), reverse=True)

    print(f"{'─'*70}")
    print(f"{'PREGUNTA':<45} {'CIERRE':>6} {'PRECIO YES':>10} {'VOL 24H':>10}")
    print(f"{'─'*70}")
    for m in upcoming[:30]:
        question  = m.get("question", "?")[:44]
        days      = m["_days_left"]
        prices    = m.get("outcomePrices", ["?"])
        if isinstance(prices, str):
            import json
            try: prices = json.loads(prices)
            except: prices = ["?"]
        price_yes = f"{float(prices[0]):.0%}" if prices and prices[0] not in ("?", None) else "?"
        vol       = float(m.get("volume24hr") or 0)
        vol_str   = f"${vol:,.0f}"
        days_str  = f"{days:.0f}d" if days >= 1 else f"{days*24:.0f}h"
        print(f"{question:<45} {days_str:>6} {price_yes:>10} {vol_str:>10}")
    print(f"{'─'*70}")
    print(f"\nTotal: {len(upcoming)} mercados próximos a cerrar.")
    print(f"Para analizar uno: py predictor.py --market-id <conditionId>")
