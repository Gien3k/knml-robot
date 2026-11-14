#!/usr/bin/env bash
# =============================================================================
#  Ekran stoiska w trybie kiosku (Raspberry Pi OS Bookworm: labwc / wayfire / X11)
#  Użycie:  ./scripts/kiosk.sh [URL]            (domyślnie http://localhost:8000)
#  Autostart konfiguruje scripts/setup_rpi.sh.
# =============================================================================
set -u
URL="${1:-http://localhost:${KIOSK_PORT:-8000}}"

BROWSER="$(command -v chromium-browser || command -v chromium || command -v google-chrome || true)"
if [ -z "$BROWSER" ]; then
  echo "Nie znaleziono Chromium. Zainstaluj: sudo apt install chromium-browser" >&2
  exit 1
fi

# czekamy, aż kontener wstanie (po starcie systemu Docker potrzebuje chwili)
until curl -sf "$URL/api/health" >/dev/null 2>&1; do sleep 2; done

# Chromium uruchamiany ponownie, gdyby się zamknął (np. po awarii sterownika GPU)
while true; do
  "$BROWSER" \
    --kiosk "$URL" \
    --noerrdialogs --disable-infobars --disable-session-crashed-bubble \
    --disable-features=Translate,TouchpadOverscrollHistoryNavigation \
    --overscroll-history-navigation=0 --disable-pinch \
    --no-first-run --password-store=basic --check-for-update-interval=31536000 \
    --ozone-platform-hint=auto
  sleep 3
done
