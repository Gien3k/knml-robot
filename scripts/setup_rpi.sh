#!/usr/bin/env bash
# =============================================================================
#  Jednorazowa konfiguracja Raspberry Pi 5 pod łazika (Raspberry Pi OS Bookworm 64-bit).
#  Idempotentny – można uruchamiać wiele razy.
#
#      sudo ./scripts/setup_rpi.sh
#
#  Co robi:
#   1. reguła udev dla OAK-D (Luxonis 03e7) + uprawnienia do portów szeregowych,
#   2. config.txt: usb_max_current_enable=1 (prąd USB dla OAK-D Pro z projektorem IR)
#      oraz dtparam=uart0=on (UART na GPIO 14/15 -> /dev/ttyAMA0 dla ESP32 RaspRover),
#   3. wyłącza konsolę szeregową na UART (inaczej logowanie "gada" z ESP32),
#   4. wyłącza ModemManager i brltty, które przejmują / "sondują" nowe porty USB-UART,
#   5. wyłącza wygaszanie ekranu,
#   6. .env: COMPOSE_FILE z nakładką docker-compose.rpi.yml (port 8000, /dev, autostart)
#      oraz ESP32_PORT=/dev/ttyAMA0 (na RaspRoverze tylko tą drogą ESP32 słucha komend ruchu),
#   7. autostart Chromium w trybie kiosku (labwc / wayfire / LXDE),
#   8. Docker i containerd startują razem z systemem (kontener ma restart: unless-stopped),
#   9. automatyczne logowanie do pulpitu – bez niego kiosk nie wystartuje po włączeniu
#      zasilania (np. po podłączeniu akumulatorów), bo czekałby na ekranie logowania.
# =============================================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Uruchom jako root: sudo $0" >&2
  exit 1
fi

REPO="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
CONFIG=/boot/firmware/config.txt;   [ -f "$CONFIG" ]  || CONFIG=/boot/config.txt
CMDLINE=/boot/firmware/cmdline.txt; [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
REBOOT=0

say()  { printf '\033[1m==> %s\033[0m\n' "$*"; }
ok()   { printf '    ok: %s\n' "$*"; }
done_() { printf '    \033[32mustawiono:\033[0m %s\n' "$*"; }

# ---------------------------------------------------------------- 1. udev
say "Reguły udev"
UDEV=/etc/udev/rules.d/80-knml-robot.rules
read -r -d '' RULES <<'EOF' || true
# KN ML łazik – OAK-D (DepthAI) bez roota; zmienia PID przy starcie (2485 -> f63b)
SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"
# mostki USB-UART LiDAR-u / ESP32: ModemManager ma ich nie dotykać
SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="1a86", ENV{ID_MM_DEVICE_IGNORE}="1"
SUBSYSTEM=="tty", ATTRS{idVendor}=="303a", ENV{ID_MM_DEVICE_IGNORE}="1"
EOF
if [ -f "$UDEV" ] && [ "$(cat "$UDEV")" = "$RULES" ]; then
  ok "$UDEV"
else
  printf '%s\n' "$RULES" > "$UDEV"
  udevadm control --reload-rules && udevadm trigger || true
  done_ "$UDEV"
fi
if id -nG "$TARGET_USER" | grep -qw dialout; then ok "$TARGET_USER w grupie dialout"; else
  usermod -aG dialout "$TARGET_USER"; done_ "$TARGET_USER -> grupa dialout"; fi

# ---------------------------------------------------------------- 2. config.txt
say "Konfiguracja rozruchu ($CONFIG)"
ensure_line() {
  local line="$1"
  if grep -Eq "^[[:space:]]*${line//./\\.}[[:space:]]*$" "$CONFIG"; then
    ok "$line"
  else
    # [all] – na wypadek, gdy plik kończy się sekcją warunkową ([cm4], [pi5] itd.)
    printf '\n[all]\n# KN ML łazik (scripts/setup_rpi.sh)\n%s\n' "$line" >> "$CONFIG"
    done_ "$line"
    REBOOT=1
  fi
}
ensure_line "usb_max_current_enable=1"
ensure_line "dtparam=uart0=on"

# ---------------------------------------------------------------- 3. konsola szeregowa
say "Konsola szeregowa na UART"
if grep -Eq 'console=(serial0|ttyAMA0),[0-9]+' "$CMDLINE"; then
  sed -i -E 's/console=(serial0|ttyAMA0),[0-9]+ ?//g' "$CMDLINE"
  done_ "usunięto console=serial0 z $CMDLINE"
  REBOOT=1
else
  ok "brak konsoli na UART"
fi
for unit in serial-getty@ttyAMA0.service serial-getty@serial0.service; do
  if systemctl is-enabled "$unit" >/dev/null 2>&1; then
    systemctl disable --now "$unit" >/dev/null 2>&1 || true
    done_ "wyłączono $unit"
  fi
done

# ---------------------------------------------------------------- 4. usługi przejmujące porty
say "Usługi, które przejmują porty USB-UART"
for unit in ModemManager.service brltty-udev.service brltty.path brltty.service; do
  if systemctl list-unit-files "$unit" >/dev/null 2>&1 && systemctl list-unit-files "$unit" | grep -q "$unit"; then
    if [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" = "masked" ]; then
      ok "$unit zamaskowany"
    else
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
      systemctl mask "$unit" >/dev/null 2>&1 || true
      done_ "zamaskowano $unit"
    fi
  fi
done

# ---------------------------------------------------------------- 5. ekran
say "Wygaszanie ekranu"
if command -v raspi-config >/dev/null 2>&1; then
  raspi-config nonint do_blanking 1 >/dev/null 2>&1 && done_ "wyłączone" || ok "pominięto"
else
  ok "brak raspi-config – pominięto"
fi

# ---------------------------------------------------------------- 6. .env + docker compose
say "Docker / .env"
ENV_FILE="$REPO/.env"
if [ ! -f "$ENV_FILE" ]; then
  cp "$REPO/.env.example" "$ENV_FILE"
  chown "$TARGET_USER": "$ENV_FILE"
  done_ "utworzono .env z .env.example"
fi
if grep -q '^COMPOSE_FILE=' "$ENV_FILE"; then
  ok "COMPOSE_FILE już ustawione"
else
  printf '\n# Raspberry Pi: zawsze z nakładką (port 8000, /dev, autostart)\nCOMPOSE_FILE=docker-compose.yml:docker-compose.rpi.yml\n' >> "$ENV_FILE"
  done_ "COMPOSE_FILE=docker-compose.yml:docker-compose.rpi.yml"
fi
# RaspRover: ESP32 wykonuje komendy ruchu tylko z UART na GPIO (/dev/ttyAMA0). Port USB
# płytki (CH343, /dev/ttyACM0) wysyła telemetrię, ale komendy przez niego NIE kręcą kołami.
if grep -Eq '^ESP32_PORT=.+' "$ENV_FILE"; then
  ok "$(grep -E '^ESP32_PORT=' "$ENV_FILE")"
elif grep -q '^ESP32_PORT=' "$ENV_FILE"; then
  sed -i 's|^ESP32_PORT=.*|ESP32_PORT=/dev/ttyAMA0|' "$ENV_FILE"; done_ "ESP32_PORT=/dev/ttyAMA0 (UART na GPIO)"
else
  printf '
# RaspRover: komendy ruchu tylko przez UART na GPIO
ESP32_PORT=/dev/ttyAMA0
' >> "$ENV_FILE"
  done_ "ESP32_PORT=/dev/ttyAMA0 (UART na GPIO)"
fi
if command -v docker >/dev/null 2>&1; then
  ok "$(docker --version)"
  if id -nG "$TARGET_USER" | grep -qw docker; then ok "$TARGET_USER w grupie docker"; else
    usermod -aG docker "$TARGET_USER"; done_ "$TARGET_USER -> grupa docker (wyloguj się i zaloguj)"; fi
else
  printf '    \033[33mbrak Dockera\033[0m – zainstaluj: curl -fsSL https://get.docker.com | sh\n'
fi

# ---------------------------------------------------------------- 7. kiosk
say "Autostart kiosku dla użytkownika $TARGET_USER"
KIOSK="$REPO/scripts/kiosk.sh"
chmod +x "$KIOSK"
as_user() { sudo -u "$TARGET_USER" "$@"; }
# labwc (domyślny na Pi 5 od końca 2024)
as_user mkdir -p "$TARGET_HOME/.config/labwc"
if as_user grep -qs "$KIOSK" "$TARGET_HOME/.config/labwc/autostart"; then ok "labwc"; else
  echo "$KIOSK &" | as_user tee -a "$TARGET_HOME/.config/labwc/autostart" >/dev/null; done_ "labwc autostart"; fi
# wayfire (starsze Bookworm)
WF="$TARGET_HOME/.config/wayfire.ini"
if [ -f "$WF" ]; then
  if grep -q "$KIOSK" "$WF"; then ok "wayfire"; else
    if grep -q '^\[autostart\]' "$WF"; then sed -i "/^\[autostart\]/a knml_kiosk = $KIOSK" "$WF"
    else printf '\n[autostart]\nknml_kiosk = %s\n' "$KIOSK" >> "$WF"; fi
    done_ "wayfire autostart"; fi
fi
# LXDE (X11)
LX="$TARGET_HOME/.config/lxsession/LXDE-pi"
if [ -d "$LX" ] || [ -d /etc/xdg/lxsession/LXDE-pi ]; then
  as_user mkdir -p "$LX"
  if as_user grep -qs "$KIOSK" "$LX/autostart"; then ok "LXDE"; else
    echo "@$KIOSK" | as_user tee -a "$LX/autostart" >/dev/null; done_ "LXDE autostart"; fi
fi

# ---------------------------------------------------------------- 8. Docker przy starcie
say "Docker przy starcie systemu"
for unit in containerd.service docker.service; do
  if systemctl list-unit-files "$unit" 2>/dev/null | grep -q "$unit"; then
    if [ "$(systemctl is-enabled "$unit" 2>/dev/null || true)" = "enabled" ]; then ok "$unit"; else
      systemctl enable "$unit" >/dev/null 2>&1 && done_ "włączono $unit"; fi
  fi
done

# ---------------------------------------------------------------- 9. logowanie do pulpitu
say "Automatyczne logowanie do pulpitu"
if command -v raspi-config >/dev/null 2>&1 && { [ -x /usr/sbin/lightdm ] || systemctl list-unit-files lightdm.service 2>/dev/null | grep -q lightdm; }; then
  if grep -Eqs "^autologin-user=$TARGET_USER$" /etc/lightdm/lightdm.conf && [ "$(systemctl get-default)" = "graphical.target" ]; then
    ok "pulpit z automatycznym logowaniem ($TARGET_USER)"
  else
    raspi-config nonint do_boot_behaviour B4 >/dev/null 2>&1 && done_ "pulpit z automatycznym logowaniem ($TARGET_USER)"
    REBOOT=1
  fi
else
  printf '    [33muwaga:[0m brak pulpitu (lightdm) – kiosk wymaga Raspberry Pi OS z pulpitem
'
fi

echo
if [ "$REBOOT" -eq 1 ]; then
  printf '\033[33mZmieniono konfigurację rozruchu – wymagany restart: sudo reboot\033[0m\n'
fi
echo "Dalej:  cd $REPO && docker compose up -d --build   (port 8000, autostart po restarcie)"
echo "Test:   docker compose exec robot python -m app.diag"
