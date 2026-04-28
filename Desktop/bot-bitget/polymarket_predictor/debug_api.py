"""
Script de diagnostico: muestra que devuelve la API de Polymarket
y por que los mercados no pasan los filtros.
"""
import json
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB  = "https://clob.polymarket.com"

print("=" * 60)
print("1. CONSULTANDO MERCADOS (Gamma API)...")
print("=" * 60)

r = requests.get(f"{GAMMA}/markets", params={"limit": 5, "active": "true", "closed": "false"}, timeout=10)
print(f"Status: {r.status_code}")
data = r.json()

# Muestra la estructura raw
if isinstance(data, list):
    markets = data
    print(f"Tipo: lista de {len(data)} mercados")
elif isinstance(data, dict):
    print(f"Tipo: dict con claves: {list(data.keys())}")
    markets = data.get("data") or data.get("markets") or []
else:
    markets = []

if markets:
    mkt = markets[0]
    print(f"\nClaves del primer mercado: {list(mkt.keys())}")
    print(f"\nPrimer mercado (raw):")
    print(json.dumps(mkt, indent=2, default=str)[:2000])
else:
    print("No se obtuvieron mercados")
    print("Respuesta completa:", json.dumps(data, indent=2, default=str)[:1000])

print("\n" + "=" * 60)
print("2. PROBANDO CLOB API (orderbook)...")
print("=" * 60)

# Intenta con el primer token que encuentre
token_id = None
if markets:
    mkt = markets[0]
    tokens = mkt.get("tokens", [])
    if tokens:
        token_id = tokens[0].get("token_id") or tokens[0].get("tokenId")
    if not token_id:
        token_id = mkt.get("token_id") or mkt.get("tokenId")

if token_id:
    print(f"Token ID encontrado: {token_id}")
    try:
        r2 = requests.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
        print(f"Orderbook status: {r2.status_code}")
        if r2.status_code == 200:
            ob = r2.json()
            print(f"Orderbook claves: {list(ob.keys())}")
            print(f"Bids: {len(ob.get('bids', []))} niveles")
            print(f"Asks: {len(ob.get('asks', []))} niveles")
    except Exception as e:
        print(f"Error CLOB: {e}")

    try:
        r3 = requests.get(f"{CLOB}/midpoint", params={"token_id": token_id}, timeout=10)
        print(f"Midpoint status: {r3.status_code} → {r3.text[:200]}")
    except Exception as e:
        print(f"Error midpoint: {e}")
else:
    print("No se encontro token_id en el primer mercado")

print("\n" + "=" * 60)
print("3. CAMPOS DE VOLUMEN Y SPREAD EN EL MERCADO")
print("=" * 60)
if markets:
    mkt = markets[0]
    vol_keys = [k for k in mkt.keys() if "vol" in k.lower() or "spread" in k.lower() or "liquid" in k.lower()]
    print(f"Campos relevantes encontrados: {vol_keys}")
    for k in vol_keys:
        print(f"  {k}: {mkt.get(k)}")
