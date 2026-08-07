#!/usr/bin/env bash
# Generate a self-signed certificate for local HTTPS.
#
# This exists so the edge can be exercised end to end — headers, redirects,
# the WebSocket upgrade, HSTS — without waiting on a public DNS name. Browsers
# will warn, and that warning is correct: nothing has verified this identity.
#
# Production uses `tls-letsencrypt.sh`. A self-signed certificate must never
# reach a real deployment, which is why this script writes a loud subject.

set -euo pipefail

TLS_DIR="${1:-infra/docker/nginx/tls}"
DAYS="${TLS_DAYS:-825}"   # the maximum most browsers accept for a leaf cert

mkdir -p "$TLS_DIR"

if [[ -f "$TLS_DIR/fullchain.pem" && "${FORCE:-0}" != "1" ]]; then
  echo "→ certificate already present at $TLS_DIR — set FORCE=1 to replace"
  exit 0
fi

openssl req -x509 -nodes -newkey rsa:2048 \
  -keyout "$TLS_DIR/privkey.pem" \
  -out "$TLS_DIR/fullchain.pem" \
  -days "$DAYS" \
  -subj "/CN=localhost/O=RetailMind LOCAL DEVELOPMENT ONLY" \
  -addext "subjectAltName=DNS:localhost,DNS:retailmind.local,IP:127.0.0.1" \
  2>/dev/null

chmod 600 "$TLS_DIR/privkey.pem"
chmod 644 "$TLS_DIR/fullchain.pem"

echo "→ self-signed certificate written to $TLS_DIR (valid $DAYS days)"
echo "  Browsers will warn. That is the point: nothing verified this identity."
