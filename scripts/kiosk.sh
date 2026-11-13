#!/usr/bin/env bash
# =============================================================================
#  Dashboard w trybie kiosku (Raspberry Pi OS Bookworm, Wayland/labwc lub X11)
#  Użycie:   ./scripts/kiosk.sh [URL]          (domyślnie http://localhost:8000)
#  Autostart (labwc):  echo "$PWD/scripts/kiosk.sh &" >> ~/.config/labwc/autostart
# =============================================================================
set -u
URL="${1:-http://localhost:${PORT:-8000}}"

# czekamy, aż kontener wstanie (po starcie systemu Docker potrzebuje chwili)
until curl -sf "$URL/api/health" >/dev/null 2>&1; do sleep 2; done

BROWSER="$(command -v chromium-browser || command -v chromium || command -v google-chrome)"
if [ -z "$BROWSER" ]; then
  echo "Nie znaleziono Chromium. Zainstaluj: sudo apt install chromium-browser" >&2
  exit 1
fi

exec "$BROWSER" \
  --kiosk "$URL" \
  --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
  --disable-features=Translate --no-first-run --password-store=basic \
  --check-for-update-interval=31536000 \
  --ozone-platform-hint=auto
