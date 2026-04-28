"""Busca un mercado por texto en la pregunta."""
import requests, json, sys

GAMMA = "https://gamma-api.polymarket.com"
query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "elon musk tweets april"

found = []
for offset in range(0, 5000, 100):
    r = requests.get(f"{GAMMA}/markets", params={
        "limit": 100, "offset": offset,
        "active": "true", "closed": "false"
    }, timeout=10)
    batch = r.json()
    if isinstance(batch, dict):
        batch = batch.get("data", [])
    if not batch:
        break
    for m in batch:
        if any(w in (m.get("question","") + m.get("description","")).lower()
               for w in query.lower().split()):
            found.append(m)
    if len(batch) < 100:
        break

print(f"Encontrados {len(found)} mercados con '{query}':\n")
for m in found:
    prices = m.get("outcomePrices", [])
    if isinstance(prices, str):
        try: prices = json.loads(prices)
        except: prices = []
    price = f"{float(prices[0]):.1%}" if prices else "?"
    print(f"Pregunta : {m.get('question','?')}")
    print(f"ID       : {m.get('conditionId','?')}")
    print(f"Precio   : {price}")
    print(f"Cierre   : {m.get('endDate','?')[:10]}")
    print(f"Vol24h   : ${float(m.get('volume24hr') or 0):,.0f}")
    print()
