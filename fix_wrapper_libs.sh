#!/bin/bash
# fix_wrapper_libs.sh — repair the wrapper's Apple libraries.
#
# wrapper-v2's pinned Apple Music 3.6.0-beta-1109 libraries are missing a
# critical symbol (you'll see "dlsym(...getPersistentKey...): undefined
# symbol" in `docker logs wrapper-v2`), which makes every ALAC download fail
# with:  KDProcessResponseCKC status: -42812  (FairPlay decrypt failed).
#
# Fix: swap the 7 broken .so files with the working versions from the
# WorldObservationLog wrapper project, then rebuild the container.
#
# Usage:  ./fix_wrapper_libs.sh
# Run this from the Music High Res project folder.

set -e
cd "$(dirname "$0")"

LIBS="libCoreFP.so libCoreLSKD.so libandroidappmusic.so libdaapkit.so libmedialibrarycore.so libmediaplatform.so libstoreservicescore.so"

if [ ! -d wrapper-v2/rootfs/system/lib64 ]; then
  echo "✗ wrapper-v2/rootfs not found. Run setup_wrapper.sh first."
  exit 1
fi

if [ ! -d wol-wrapper/rootfs/system/lib64 ]; then
  echo "→ Cloning the wrapper with working libraries (WorldObservationLog)…"
  rm -rf wol-wrapper
  git clone --depth 1 https://github.com/WorldObservationLog/wrapper.git wol-wrapper
fi

echo "→ Swapping broken libraries…"
for f in $LIBS; do
  if [ -f "wol-wrapper/rootfs/system/lib64/$f" ]; then
    cp "wol-wrapper/rootfs/system/lib64/$f" "wrapper-v2/rootfs/system/lib64/$f"
    echo "   ✓ $f"
  else
    echo "   ! $f missing in wol-wrapper — skipping"
  fi
done

echo "→ Rebuilding wrapper container…"
cd wrapper-v2
docker compose up -d --build

echo
echo "✓ Done. Verify with:  docker logs --tail 20 wrapper-v2   (no more dlsym warnings)"
echo "  Then test:  curl http://127.0.0.1/health"
