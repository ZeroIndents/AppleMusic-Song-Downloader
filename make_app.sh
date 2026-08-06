#!/bin/bash
# make_app.sh — build "Music High Res.app", a double-clickable Mac app.
#
# Creates a proper .app bundle (icon + Dock presence) whose launcher boots the
# whole stack (Docker → ALAC wrapper → app server) and opens the UI in a
# standalone app-style browser window.
#
# Usage:  ./make_app.sh
set -e
cd "$(dirname "$0")"

APP="Music High Res.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
RES="$CONTENTS/Resources"

echo "━━━ Building $APP ━━━"

# 1. Pillow for the icon (project-local)
if ! .venv/bin/python -c 'import PIL' 2>/dev/null; then
  echo "→ installing pillow (icon rendering)…"
  .venv/bin/pip install --quiet pillow
fi

# 2. Generate the icon
echo "→ generating icon…"
.venv/bin/python - <<'PY'
from PIL import Image, ImageDraw
import math

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# diagonal gradient: #fa2d48 → #af52de → #5e5ce6
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * S)
        if t < 0.5:
            f = t * 2
            c = (250 + (175 - 250) * f, 45 + (82 - 45) * f, 72 + (222 - 72) * f)
        else:
            f = (t - 0.5) * 2
            c = (175 + (94 - 175) * f, 82 + (92 - 82) * f, 222 + (230 - 222) * f)
        img.putpixel((x, y), (int(c[0]), int(c[1]), int(c[2]), 255))
# rounded corners mask
mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, S - 1, S - 1], radius=185, fill=255)
img.putalpha(mask)

# music note (double eighth note) drawn with primitives
note = Image.new("RGBA", (S, S), (0, 0, 0, 0))
nd = ImageDraw.Draw(note)
white = (255, 255, 255, 255)
head_w, head_h = 150, 112
x1, y1 = 330, 560   # left note head center
x2, y2 = 640, 500   # right note head center
def head(cx, cy):
    nd.ellipse([cx - head_w // 2, cy - head_h // 2, cx + head_w // 2, cy + head_h // 2], fill=white)
head(x1, y1); head(x2, y2)
# stems (from head tops up)
def stem(cx, cy, top):
    nd.rectangle([cx - 16, top, cx + 16, cy - head_h // 2 + 8], fill=white)
stem(x1, y1, 260); stem(x2, y2, 260)
# beam connecting stem tops
nd.polygon([(x1 - 16, 260), (x2 + 16, 260), (x2 + 16, 300), (x1 - 16, 300)], fill=white)
# gentle rotation for style
note = note.rotate(-8, resample=Image.BICUBIC, center=(S // 2, S // 2))
img = Image.alpha_composite(img, note)

# write iconset
import os
os.makedirs("/tmp/mhr-icon.iconset", exist_ok=True)
sizes = [(16, "icon_16x16.png"), (32, "icon_16x16@2x.png"), (32, "icon_32x32.png"),
         (64, "icon_32x32@2x.png"), (128, "icon_128x128.png"), (256, "icon_128x128@2x.png"),
         (256, "icon_256x256.png"), (512, "icon_256x256@2x.png"), (512, "icon_512x512.png"),
         (1024, "icon_512x512@2x.png")]
for size, name in sizes:
    img.resize((size, size), Image.LANCZOS).save(f"/tmp/mhr-icon.iconset/{name}")
print("  iconset written")
PY
mkdir -p "$MACOS" "$RES"
iconutil -c icns /tmp/mhr-icon.iconset -o "$RES/AppIcon.icns"
rm -rf /tmp/mhr-icon.iconset
echo "  icon → AppIcon.icns"

# 3. Launcher executable
cat > "$MACOS/MusicHighRes" <<'SH'
#!/bin/bash
# Music High Res — app launcher (inside the .app bundle).
# Boots Docker → ALAC wrapper → app server, then opens the UI in a standalone
# app-style window. The app stays alive in the Dock while the server runs.
set -u
PROJECT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$PROJECT" || exit 1

log() { echo "[Music High Res] $*"; }

# ── 1. Docker Desktop ──────────────────────────────────────────────────
if ! docker info >/dev/null 2>&1; then
  log "starting Docker Desktop…"
  open -a Docker 2>/dev/null
  WAIT=0
  until docker info >/dev/null 2>&1; do
    WAIT=$((WAIT+1))
    [ "$WAIT" -ge 120 ] && break
    sleep 2
  done
fi
docker info >/dev/null 2>&1 && log "docker ready" || log "WARNING: docker not ready"

# ── 2. ALAC wrapper ────────────────────────────────────────────────────
if [ -d "$PROJECT/wrapper-v2" ] && docker info >/dev/null 2>&1; then
  ( cd "$PROJECT/wrapper-v2" && docker compose up -d ) >/dev/null 2>&1
  WAIT=0
  while [ "$WAIT" -lt 30 ]; do
    S=$(curl -s -m 2 http://127.0.0.1/me 2>/dev/null | grep -o '"state":"[a-z_]*"' | head -1 | cut -d'"' -f4)
    [ "$S" = "authenticated" ] && break
    WAIT=$((WAIT+1)); sleep 2
  done
  log "wrapper ready"
fi

# ── 3. App server ──────────────────────────────────────────────────────
.venv/bin/python "$PROJECT/app.py" &
APP_PID=$!
trap 'kill "$APP_PID" 2>/dev/null; exit 0' SIGTERM SIGINT

# wait for the server, then open the UI in an app-style window
for _ in $(seq 1 30); do
  if curl -s -o /dev/null http://127.0.0.1:8741/api/status 2>/dev/null; then
    break
  fi
  sleep 1
done
BROWSER_OPENED=0
for BROWSER in "Brave Browser" "Google Chrome" "Microsoft Edge" "Arc"; do
  if [ -d "/Applications/$BROWSER.app" ]; then
    open -na "$BROWSER" --args --app=http://127.0.0.1:8741 2>/dev/null && BROWSER_OPENED=1
    break
  fi
done
[ "$BROWSER_OPENED" = "1" ] || open http://127.0.0.1:8741 2>/dev/null
log "running → http://127.0.0.1:8741"

wait "$APP_PID"
SH
chmod +x "$MACOS/MusicHighRes"

# 4. Info.plist
cat > "$CONTENTS/Info.plist" <<'PL'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>            <string>Music High Res</string>
    <key>CFBundleDisplayName</key>     <string>Music High Res</string>
    <key>CFBundleIdentifier</key>      <string>com.musichighres.app</string>
    <key>CFBundleExecutable</key>      <string>MusicHighRes</string>
    <key>CFBundleIconFile</key>        <string>AppIcon</string>
    <key>CFBundlePackageType</key>     <string>APPL</string>
    <key>CFBundleShortVersionString</key> <string>1.0</string>
    <key>CFBundleVersion</key>         <string>1</string>
    <key>LSMinimumSystemVersion</key>  <string>12.0</string>
    <key>NSHighResolutionCapable</key> <true/>
    <key>LSApplicationCategoryType</key> <string>public.app-category.music</string>
</dict>
</plist>
PL

echo
echo "✓ Built: $APP"
echo "  Double-click it from Finder to run like a normal app."
