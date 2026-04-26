#!/usr/bin/env bash
# Pre-flight for SIP mode: ensure EXTERNAL_IP in .env matches the host's
# actual LAN IP on the FritzBox subnet. If it drifted (DHCP renewal, network
# change, etc.), update .env and recreate the Asterisk container so its
# REGISTER advertises a Contact header FritzBox can reach.
set -euo pipefail

cd "$(dirname "$0")"
ENV_FILE=".env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "[preflight] $ENV_FILE not found — run: cp .env.example .env && edit it" >&2
  exit 1
fi

# shellcheck disable=SC1090
. "$ENV_FILE"

# Derive the FritzBox /24 subnet from FRITZBOX_HOST (e.g. 192.168.178.1 → 192.168.178).
subnet="${FRITZBOX_HOST%.*}"
if [[ -z "$subnet" || "$subnet" == "$FRITZBOX_HOST" ]]; then
  echo "[preflight] cannot derive subnet from FRITZBOX_HOST=$FRITZBOX_HOST" >&2
  exit 1
fi

# Pick the host's IPv4 on that subnet. Works with macOS ifconfig and Linux iproute2.
actual_ip=$(
  { ifconfig 2>/dev/null || ip -4 addr 2>/dev/null; } \
    | awk -v s="$subnet" '/inet / && $2 ~ "^"s"[.]" {sub(/\/[0-9]+$/, "", $2); print $2; exit}'
)

if [[ -z "$actual_ip" ]]; then
  echo "[preflight] no host IPv4 found on $subnet.0/24 — is this machine on the FritzBox LAN?" >&2
  exit 1
fi

if [[ "${EXTERNAL_IP:-}" == "$actual_ip" ]]; then
  echo "[preflight] EXTERNAL_IP=$EXTERNAL_IP matches host LAN IP — ok"
  exit 0
fi

echo "[preflight] EXTERNAL_IP drifted: ${EXTERNAL_IP:-<unset>} → $actual_ip"
if [[ "$(uname)" == "Darwin" ]]; then
  sed -i '' "s|^EXTERNAL_IP=.*|EXTERNAL_IP=$actual_ip|" "$ENV_FILE"
else
  sed -i "s|^EXTERNAL_IP=.*|EXTERNAL_IP=$actual_ip|" "$ENV_FILE"
fi
echo "[preflight] $ENV_FILE updated"

if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'agent-asterisk'; then
  echo "[preflight] recreating agent-asterisk to pick up new EXTERNAL_IP"
  docker compose up -d --force-recreate asterisk >/dev/null
  echo "[preflight] container recreated — allow ~5s for re-registration with FritzBox"
else
  echo "[preflight] agent-asterisk is not running; start it with: cd services/telephony && docker compose up"
fi
