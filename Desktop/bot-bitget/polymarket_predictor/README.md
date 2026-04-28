# Polymarket Predictor

Algoritmo avanzado de predicción de mercados para Polymarket usando análisis de flujo de órdenes, momentum, calibración estadística y actualización bayesiana.

## Arquitectura

```
polymarket_predictor/
├── api/
│   └── client.py          # Cliente REST de Polymarket CLOB API
├── signals/
│   ├── orderflow.py       # Análisis de libro de órdenes y trades
│   └── momentum.py        # RSI, VWAP, reversión a la media
├── models/
│   ├── bayesian.py        # Actualizador bayesiano (distribución Beta)
│   ├── calibration.py     # Calibración histórica + ajuste de liquidez
│   └── ensemble.py        # Meta-modelo (XGBoost o lineal ponderado)
├── risk/
│   └── kelly.py           # Kelly Criterion fraccionado + gestión de portafolio
├── predictor.py           # Orquestador principal
└── config.py              # Parámetros configurables
```

## Pipeline de predicción

1. **Datos** — orderbook, trades recientes, metadatos del mercado
2. **Señales** — order imbalance, VWAP, momentum, RSI, time decay
3. **Bayesiano** — actualiza P(YES) con cada señal usando distribución Beta
4. **Calibración** — corrige sesgos históricos por tipo de mercado
5. **Ensemble** — combina todas las señales (XGBoost si hay datos, lineal si no)
6. **Kelly** — calcula tamaño óptimo de posición fraccionado

## Instalación

```bash
cd polymarket_predictor
pip install -r requirements.txt
```

## Uso

```bash
# Escanear todos los mercados activos
python predictor.py --bankroll 1000 --limit 50

# Solo mercados de política
python predictor.py --bankroll 1000 --type politics

# Analizar un mercado específico
python predictor.py --market-id <condition_id>
```

## Parámetros clave (`config.py`)

| Parámetro | Default | Descripción |
|---|---|---|
| `EDGE_THRESHOLD` | 0.04 | Edge mínimo para recomendar (4%) |
| `KELLY_FRACTION` | 0.25 | Kelly fraccionado (25% del Kelly teórico) |
| `MAX_POSITION_USD` | 100 | Máximo por posición en USD |
| `MAX_PORTFOLIO_EXPOSURE` | 0.30 | Exposición máxima del portafolio (30%) |
| `MAX_SPREAD_PCT` | 0.08 | Spread máximo para considerar un mercado (8%) |
