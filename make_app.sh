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
from PIL import Image, ImageDraw, ImageFilter
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
# soft light halo behind the note + a top-left spotlight for depth
halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
hd = ImageDraw.Draw(halo)
hd.ellipse([240, 220, 784, 700], fill=(255, 255, 255, 42))
halo = halo.filter(ImageFilter.GaussianBlur(90))
img = Image.alpha_composite(img, halo)
spot = Image.new("RGBA", (S, S), (0, 0, 0, 0))
sd = ImageDraw.Draw(spot)
sd.ellipse([-300, -300, 620, 620], fill=(255, 255, 255, 30))
spot = spot.filter(ImageFilter.GaussianBlur(180))
img = Image.alpha_composite(img, spot)

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

# rounded corners mask (applied last so the glows stay inside the tile)
mask = Image.new("L", (S, S), 0)
md = ImageDraw.Draw(mask)
md.rounded_rectangle([0, 0, S - 1, S - 1], radius=185, fill=255)
img.putalpha(mask)

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
# Delegates to the universal start.sh --app-style: boots Docker → ALAC
# wrapper → app server, then opens the UI in a standalone app-style window.
# The app stays alive in the Dock while the server runs.
set -u
PROJECT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$PROJECT" || exit 1

# launchd hands .app bundles a minimal PATH (/usr/bin:/bin:/usr/sbin:/sbin)
# that does NOT include Homebrew's /usr/local/bin (or /opt/homebrew/bin on
# Apple Silicon). start.sh sets a sane PATH itself, but set it here too so
# the `bash` invocation below can find everything regardless.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

# One source of truth for the whole boot sequence: start.sh. It also keeps
# this process alive (waits on the server PID) so the app stays in the Dock
# until the user right-clicks → Quit.
exec bash "$PROJECT/start.sh" --app-style
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
