"""Analiza un mercado de Polymarket por su slug de URL."""
import sys, requests, json

GAMMA = "https://gamma-api.polymarket.com"
slug = sys.argv[1] if len(sys.argv) > 1 else "jimmy-kimmel-firedresigns-by-may-31"

# Busca por slug
r = requests.get(f"{GAMMA}/events", params={"slug": slug}, timeout=10)
data = r.json()
events = data if isinstance(data, list) else data.get("data", [data] if isinstance(data, dict) else [])

market = None
for event in events:
    for m in event.get("markets", []):
        market = m
        break
    if market:
        break

# Si no encuentra por events, busca por markets directamente
if not market:
    r2 = requests.get(f"{GAMMA}/markets", params={"slug": slug}, timeout=10)
    d2 = r2.json()
    markets = d2 if isinstance(d2, list) else d2.get("data", [])
    if markets:
        market = markets[0]

if not market:
    # Último intento: buscar en todos
    print("Buscando en catálogo completo...")
    for offset in range(0, 2000, 100):
        r3 = requests.get(f"{GAMMA}/markets", params={"limit":100,"offset":offset,"active":"true"}, timeout=10)
        batch = r3.json()
        if isinstance(batch, dict): batch = batch.get("data",[])
        if not batch: break
        for m in batch:
            if slug in (m.get("slug","") + m.get("groupItemTitle","")).lower():
                market = m
                break
        if market: break

if not market:
    print(f"No encontrado: {slug}")
    sys.exit(1)

prices = market.get("outcomePrices", [])
if isinstance(prices, str):
    try: prices = json.loads(prices)
    except: prices = []

print(f"Mercado   : {market.get('question','?')}")
print(f"ID        : {market.get('conditionId','?')}")
print(f"Precio YES: {float(prices[0]):.1%}" if prices else "Precio: ?")
print(f"Vol 24h   : ${float(market.get('volume24hr') or 0):,.0f}")
print(f"Cierre    : {market.get('endDate','?')[:10]}")
print(f"\nconditionId para predictor:")
print(market.get('conditionId','?'))
