#!/bin/bash
# Auto-actualiza el servidor desde GitHub y reinicia el bot.
# Usar en el servidor: bash update.sh
# O configurar como cron: 0 4 * * * /opt/predictor/update.sh >> /var/log/predictor_update.log 2>&1

set -e

REPO_DIR="/opt/polymarket"
PREDICTOR_DIR="/opt/predictor"

echo "[$(date)] Iniciando actualización..."

cd "$REPO_DIR"
git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" = "$REMOTE" ]; then
    echo "[$(date)] Sin cambios. Nada que actualizar."
    exit 0
fi

echo "[$(date)] Nuevos commits detectados. Actualizando..."
git pull origin main

echo "[$(date)] Reconstruyendo imagen Docker..."
cd "$PREDICTOR_DIR"
docker compose pull 2>/dev/null || true
docker compose up -d --build

echo "[$(date)] ✓ Actualización completada."
docker compose ps
