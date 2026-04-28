"""Muestra las categorías y tags disponibles en Polymarket."""
import requests

GAMMA = "https://gamma-api.polymarket.com"

# Tags disponibles
print("=== TAGS ===")
r = requests.get(f"{GAMMA}/tags", timeout=10)
if r.status_code == 200:
    tags = r.json()
    if isinstance(tags, list):
        for t in tags:
            print(f"  {t.get('id','?'):4} | {t.get('label') or t.get('name') or t.get('slug','?')}")
    else:
        print(tags)
else:
    print(f"Status {r.status_code}: {r.text[:300]}")

# Mercados con tag elon
print("\n=== MERCADOS CON 'elon' ===")
r2 = requests.get(f"{GAMMA}/markets", params={"limit": 5, "tag": "elon"}, timeout=10)
print(f"Status: {r2.status_code}")
d = r2.json()
markets = d if isinstance(d, list) else d.get("data", d.get("markets", []))
for m in markets[:5]:
    print(f"  {m.get('question','?')[:80]}")

# Mercados con tag politics/geopolitics
print("\n=== MERCADOS CON 'politics' ===")
r3 = requests.get(f"{GAMMA}/markets", params={"limit": 5, "tag": "politics"}, timeout=10)
d3 = r3.json()
markets3 = d3 if isinstance(d3, list) else d3.get("data", d3.get("markets", []))
for m in markets3[:5]:
    print(f"  {m.get('question','?')[:80]}")

# Intenta buscar por keyword
print("\n=== BUSQUEDA POR KEYWORD 'elon' ===")
r4 = requests.get(f"{GAMMA}/markets", params={"limit": 5, "q": "elon"}, timeout=10)
d4 = r4.json()
markets4 = d4 if isinstance(d4, list) else d4.get("data", d4.get("markets", []))
for m in markets4[:5]:
    print(f"  {m.get('question','?')[:80]}")

print("\n=== BUSQUEDA POR KEYWORD 'geopolit' ===")
r5 = requests.get(f"{GAMMA}/markets", params={"limit": 5, "q": "war"}, timeout=10)
d5 = r5.json()
markets5 = d5 if isinstance(d5, list) else d5.get("data", d5.get("markets", []))
for m in markets5[:5]:
    print(f"  {m.get('question','?')[:80]}")
