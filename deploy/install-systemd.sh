#!/bin/bash
# Установка systemd-сервиса tbottabak (запускать на VPS из корня репозитория).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UNIT_NAME="tbottabak.service"
UNIT_DST="/etc/systemd/system/${UNIT_NAME}"
SERVICE_USER="tbottabak"

if [[ ! -f "${ROOT}/.venv/bin/python" ]]; then
  echo "Нет ${ROOT}/.venv — сначала: python3 -m venv .venv && pip install -r requirements.txt"
  exit 1
fi

if [[ ! -f "${ROOT}/.env" ]]; then
  echo "Нет ${ROOT}/.env — скопируйте: cp .env.example .env && nano .env"
  exit 1
fi

if ! id "${SERVICE_USER}" &>/dev/null; then
  echo "Создаю системного пользователя ${SERVICE_USER}…"
  sudo useradd -r -m -d /opt/tbottabak "${SERVICE_USER}" 2>/dev/null || \
    sudo useradd -r "${SERVICE_USER}"
fi

sudo chown -R "${SERVICE_USER}:${SERVICE_USER}" "${ROOT}"

sed "s|/opt/tbottabak|${ROOT}|g" "${ROOT}/deploy/tbottabak.service" | sudo tee "${UNIT_DST}" > /dev/null

sudo systemctl daemon-reload
sudo systemctl enable "${UNIT_NAME}"
sudo systemctl restart "${UNIT_NAME}"
sudo systemctl status "${UNIT_NAME}" --no-pager

echo ""
echo "Логи: journalctl -u ${UNIT_NAME} -f"
