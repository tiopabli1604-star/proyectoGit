"""
Intenta contar tweets de Elon Musk del 21 al 28 de abril usando fuentes públicas.
"""
import requests
from datetime import datetime, timezone

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Intenta Nitter (mirror público de X)
nitter_instances = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.cz",
]

print("Buscando tweets de Elon Musk (21-28 abril 2026)...\n")

found = False
for instance in nitter_instances:
    try:
        url = f"{instance}/elonmusk/search?q=from%3Aelonmusk+since%3A2026-04-21+until%3A2026-04-28&f=tweets"
        r = requests.get(url, headers=headers, timeout=8)
        if r.status_code == 200 and "tweet" in r.text.lower():
            # Cuenta ocurrencias de tweet-content o similar
            count = r.text.lower().count('class="tweet-content')
            print(f"✓ {instance} → {count} tweets en esta página")
            found = True
            break
        else:
            print(f"✗ {instance} → {r.status_code}")
    except Exception as e:
        print(f"✗ {instance} → {e}")

if not found:
    print("\nNitter no disponible.")
    print("\nOPCIÓN MANUAL (tarda 2 minutos):")
    print("1. Abre: https://x.com/search?q=from%3Aelonmusk+since%3A2026-04-21+until%3A2026-04-29&src=typed_query&f=live")
    print("2. Cuenta los tweets que aparecen (o estima scrolleando)")
    print("\nAlternativa: mira https://xtracker.io o similar para estadísticas de @elonmusk")
    print("\nEl mercado dice 220-259 tweets.")
    print("Si el número real es conocido, apuesta al rango exacto antes de que cierre.")
