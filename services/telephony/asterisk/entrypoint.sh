#!/bin/sh
set -e

: "${FRITZBOX_HOST:?FRITZBOX_HOST not set}"
: "${FRITZBOX_USER:?FRITZBOX_USER not set}"
: "${FRITZBOX_PASS:?FRITZBOX_PASS not set}"
: "${EXTERNAL_IP:?EXTERNAL_IP not set}"

for f in /etc/asterisk-templates/*; do
  name=$(basename "$f")
  if [ "${name##*.}" = "tmpl" ]; then
    out="/etc/asterisk/${name%.tmpl}"
    envsubst < "$f" > "$out"
  else
    cp "$f" "/etc/asterisk/$name"
  fi
done

exec "$@"
