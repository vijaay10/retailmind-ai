#!/usr/bin/env bash
# Obtain and renew a real certificate via Let's Encrypt.
#
# Two things this script is careful about:
#
#   1. It uses the *staging* endpoint unless STAGING=0. Let's Encrypt rate
#      limits are per-domain per-week, and a misconfigured first attempt can
#      lock you out of issuance for days. Prove the plumbing on staging, then
#      switch.
#
#   2. It writes into the same volume nginx reads, and reloads rather than
#      restarts. A restart drops every open Streamlit session; a reload does
#      not.

set -euo pipefail

DOMAIN="${RM_DOMAIN:?set RM_DOMAIN to the public hostname}"
EMAIL="${RM_ACME_EMAIL:?set RM_ACME_EMAIL for expiry notices}"
COMPOSE="docker compose -f infra/compose/compose.yml -f infra/compose/compose.prod.yml"

STAGING_FLAG="--staging"
if [[ "${STAGING:-1}" == "0" ]]; then
  STAGING_FLAG=""
  echo "→ requesting a PRODUCTION certificate for $DOMAIN"
else
  echo "→ requesting a STAGING certificate for $DOMAIN (set STAGING=0 when the plumbing works)"
fi

# The webroot must be reachable over plain HTTP: the edge deliberately does not
# redirect /.well-known/acme-challenge/, so renewal still works when the
# current certificate has already expired.
$COMPOSE run --rm --entrypoint certbot certbot \
  certonly --webroot --webroot-path /var/www/certbot \
  $STAGING_FLAG \
  --email "$EMAIL" --agree-tos --no-eff-email \
  -d "$DOMAIN" \
  --cert-name retailmind

# nginx reads fullchain.pem/privkey.pem; certbot writes into live/<name>/.
$COMPOSE exec -T edge sh -c '
  cp -L /etc/letsencrypt/live/retailmind/fullchain.pem /etc/nginx/tls/fullchain.pem &&
  cp -L /etc/letsencrypt/live/retailmind/privkey.pem  /etc/nginx/tls/privkey.pem &&
  nginx -t && nginx -s reload
'

echo "→ certificate installed and nginx reloaded without dropping sessions"
echo "  Renewal: this script is idempotent — run it from cron twice a month."
