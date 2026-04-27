import os
from dotenv import load_dotenv

load_dotenv()

# Polymarket API endpoints
CLOB_API_URL = "https://clob.polymarket.com"
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_WS_URL = "wss://clob.polymarket.com/ws"

# Credenciales (opcional para endpoints públicos)
POLY_API_KEY = os.getenv("POLY_API_KEY", "")
POLY_SECRET = os.getenv("POLY_SECRET", "")
POLY_PASSPHRASE = os.getenv("POLY_PASSPHRASE", "")

# Parámetros del modelo
MIN_LIQUIDITY_USD = 500          # Liquidez mínima para considerar un mercado
MIN_VOLUME_24H = 1000            # Volumen mínimo 24h en USD
MAX_SPREAD_PCT = 0.08            # Spread máximo permitido (8%)
EDGE_THRESHOLD = 0.04            # Edge mínimo para operar (4%)

# Kelly Criterion
KELLY_FRACTION = 0.25            # Kelly fraccionado (25% del Kelly completo)
MAX_POSITION_USD = 100           # Posición máxima por mercado en USD
MAX_PORTFOLIO_EXPOSURE = 0.30    # Exposición máxima del portafolio (30%)

# Señales — pesos del ensemble
SIGNAL_WEIGHTS = {
    "orderflow":   0.35,
    "momentum":    0.25,
    "calibration": 0.25,
    "bayesian":    0.15,
}

# Resolución de mercados: ventana de features
LOOKBACK_TRADES = 200            # Últimas N operaciones para features
LOOKBACK_MINUTES = 60            # Ventana temporal en minutos
