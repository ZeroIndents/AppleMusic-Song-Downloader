#!/bin/bash
# login2fa.sh - Fresh Apple ID login + submit the new 2FA code in one go.
#
#   ./login2fa.sh
#
# 1. Restarts the wrapper so it starts a BRAND NEW login (a new code is sent
#    to your Gmail / trusted device - old codes can't be reused).
# 2. Waits for the wrapper to ask for the code.
# 3. Asks you for the NEW 6-digit code and submits it immediately.

set -e
cd "$(dirname "$0")/wrapper-v2"

echo "━━━ Fresh login + 2FA ━━━"
echo "→ Restarting wrapper to start a new login…"
docker compose up -d --force-recreate >/dev/null 2>&1

echo "→ Waiting for the wrapper to request the code…"
for i in $(seq 1 30); do
  ME=$(curl -s -m 3 http://127.0.0.1/me 2>/dev/null || true)
  if echo "$ME" | grep -q 'awaiting_2fa'; then
    echo "  ✓ Code requested — check your Gmail / trusted device NOW"
    break
  fi
  sleep 2
done

if ! echo "$ME" | grep -q 'awaiting_2fa'; then
  echo "✗ Wrapper didn't reach the 2FA step. Status was: $ME"
  echo "  Check: docker logs --tail 30 wrapper-v2"
  exit 1
fi

read -r -p "Enter the NEW 6-digit code: " CODE
echo "→ Submitting…"
RESP=$(curl -s -X POST http://127.0.0.1/login/2fa \
  -H 'Content-Type: application/json' \
  -d "{\"code\":\"$CODE\"}")
echo "response: $RESP"
echo
sleep 2
echo "=== final /me ==="
curl -s -m 5 http://127.0.0.1/me
echo
if curl -s -m 5 http://127.0.0.1/me | grep -q 'awaiting_2fa'; then
  echo "⚠ Still awaiting the code — it may have been mistyped or arrived too late. Try ./login2fa.sh again (a fresh code will be sent)."
elif curl -s -m 5 http://127.0.0.1/me | grep -q 'failed'; then
  echo "⚠ Login failed. Try ./login2fa.sh again with the freshest code."
else
  echo "🎉 Logged in! The wrapper is ready for ALAC downloads."
fi
