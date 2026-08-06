#!/bin/bash
# login_wrapper.sh - Log your Apple ID into the gamdl wrapper (run in YOUR terminal).
#
#   ./login_wrapper.sh
#
# Prompts for your Apple ID email + password, stores them in wrapper-v2/.env
# (chmod 600, stays on this machine), restarts the wrapper so it logs in, and
# shows the result. If your account has 2FA, follow the printed instructions.

set -e
cd "$(dirname "$0")/wrapper-v2"

echo "━━━ Apple ID login for the wrapper ━━━"
read -r -p "Apple ID email: " EMAIL
read -r -s -p "Apple ID password (hidden): " PASS
echo

if [ -z "$EMAIL" ] || [ -z "$PASS" ]; then
  echo "✗ Empty email or password - aborting."
  exit 1
fi

umask 077
cat > .env <<EOF
WRAPPER_USERNAME=$EMAIL
WRAPPER_PASSWORD=$PASS
EOF
chmod 600 .env
echo "→ Credentials saved to wrapper-v2/.env (local only). Restarting wrapper…"

docker compose up -d --force-recreate

echo "→ Waiting for the wrapper to log in…"
for i in $(seq 1 20); do
  ME=$(curl -s -m 3 http://127.0.0.1/me 2>/dev/null)
  if [ -n "$ME" ] && ! echo "$ME" | grep -qi 'not logged\|null\|error'; then
    break
  fi
  sleep 2
done

echo
echo "=== login status (/me) ==="
echo "${ME:-no response}"
echo
case "$ME" in
  *2fa*|*2FA*|*code*|*202*)
    echo "→ 2FA required. Apple sent you a code — run:"
    echo "    curl -X POST http://127.0.0.1/login/2fa -H 'Content-Type: application/json' -d '{\"code\":\"123456\"}'"
    echo "  (replace 123456 with your code, then re-run ./login_wrapper.sh to verify)"
    ;;
  *"Apple Music"*|*account*|*storefront*|*"apple_id"*|*"appleId"*)
    echo "✓ Logged in. The wrapper is ready for ALAC downloads."
    ;;
  *)
    echo "Check the result above. If it looks empty, run: docker logs --tail 30 wrapper-v2"
    ;;
esac
