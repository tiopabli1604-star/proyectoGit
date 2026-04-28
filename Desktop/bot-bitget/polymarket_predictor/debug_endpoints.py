"""Prueba diferentes endpoints para obtener más mercados."""
import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB  = "https://clob.polymarket.com"

# 1. Cuántos mercados hay en total
print("=== TOTAL DE MERCADOS ===")
r = requests.get(f"{GAMMA}/markets", params={"limit": 1, "active": "true", "closed": "false"}, timeout=10)
data = r.json()
if isinstance(data, dict):
    print(f"Claves respuesta: {list(data.keys())}")
    print(f"Count/total: {data.get('count') or data.get('total') or data.get('pagination')}")
else:
    print(f"Lista de {len(data)} items")

# 2. Probamos paginación con offset alto
print("\n=== MERCADOS CON OFFSET 500 ===")
r2 = requests.get(f"{GAMMA}/markets", params={"limit": 5, "offset": 500, "active": "true"}, timeout=10)
d2 = r2.json()
markets2 = d2 if isinstance(d2, list) else d2.get("data", [])
for m in markets2[:5]:
    print(f"  {m.get('question','?')[:70]}")

# 3. Endpoint de events
print("\n=== EVENTS API ===")
r3 = requests.get(f"{GAMMA}/events", params={"limit": 5, "active": "true"}, timeout=10)
print(f"Status: {r3.status_code}")
if r3.status_code == 200:
    d3 = r3.json()
    events = d3 if isinstance(d3, list) else d3.get("data", [])
    for e in events[:5]:
        print(f"  {e.get('title','?')[:60]}")
        markets_in_event = e.get("markets", [])
        print(f"    → {len(markets_in_event)} mercados")

# 4. CLOB markets
print("\n=== CLOB MARKETS ===")
r4 = requests.get(f"{CLOB}/markets", params={"limit": 5}, timeout=10)
print(f"Status: {r4.status_code}")
if r4.status_code == 200:
    d4 = r4.json()
    markets4 = d4.get("data", []) if isinstance(d4, dict) else d4
    for m in markets4[:3]:
        print(f"  {str(m)[:100]}")

# 5. Búsqueda por slug/categoría
print("\n=== MERCADOS CON CURSOR ===")
r5 = requests.get(f"{GAMMA}/markets", params={"limit": 5, "offset": 200, "active": "true", "closed": "false"}, timeout=10)
d5 = r5.json()
markets5 = d5 if isinstance(d5, list) else d5.get("data", [])
print(f"Offset 200 devuelve: {len(markets5)} mercados")
for m in markets5[:5]:
    print(f"  {m.get('question','?')[:70]}")
