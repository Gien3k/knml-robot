#!/usr/bin/env bash
# =============================================================================
#  Wgrywa bieżącą wersję z laptopa na łazika (Raspberry Pi 5) i ją uruchamia.
#  Uruchamiać z laptopa (Git Bash / Linux / macOS):
#
#      ./scripts/deploy_rpi.sh                 # wgraj, skonfiguruj, zbuduj, uruchom
#      ./scripts/deploy_rpi.sh --reboot        # + restart Pi i sprawdzenie autostartu
#                                              #   (to samo, co podłączenie akumulatorów)
#
#  Domyślnie: knml@lazik.local, klucz ~/.ssh/knml_lazik, katalog ~/knml-robot.
#  Zmiana: KNML_HOST=user@adres KNML_KEY=~/.ssh/klucz ./scripts/deploy_rpi.sh
#  Ustawienia z .env i kalibracja z data/ na Pi zostają, poprzednia wersja -> ~/knml-robot.old
# =============================================================================
set -euo pipefail

HOST="${KNML_HOST:-knml@lazik.local}"
KEY="${KNML_KEY:-$HOME/.ssh/knml_lazik}"
REBOOT_TEST=0
[ "${1:-}" = "--reboot" ] && REBOOT_TEST=1
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -i "$KEY" -o ConnectTimeout=6 -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15 "$HOST")

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
wait_ssh() {
  local limit="$1"
  for ((i = 0; i < limit; i += 5)); do
    if "${SSH[@]}" true 2>/dev/null; then return 0; fi
    sleep 5
  done
  return 1
}

say "Czekam na łazika ($HOST)"
if ! wait_ssh 300; then
  echo "Brak połączenia z $HOST po 5 minutach. Czy Pi jest włączone i w tej samej sieci co laptop?" >&2
  exit 1
fi

say "Wysyłam aktualną wersję"
tar -C "$REPO" -czf - \
  --exclude=.git --exclude=node_modules --exclude=frontend/dist --exclude=.env --exclude=./data \
  --exclude=__pycache__ --exclude=.pytest_cache . \
  | "${SSH[@]}" 'rm -rf ~/knml-robot.new && mkdir -p ~/knml-robot.new && tar -xzf - -C ~/knml-robot.new'

"${SSH[@]}" 'bash -s' <<'REMOTE'
set -euo pipefail
cd ~/knml-robot.new
# końce linii jak w Linuksie (pliki z Windowsa)
find . -type f \( -name '*.sh' -o -name '*.py' -o -name '*.yml' -o -name '*.yaml' -o -name '*.js' \
  -o -name '*.css' -o -name '*.html' -o -name '*.md' -o -name '*.ini' -o -name '*.txt' \
  -o -name 'Dockerfile' -o -name '.env.example' -o -name '.dockerignore' \) -exec sed -i 's/\r$//' {} +
chmod +x scripts/*.sh
[ -f ~/knml-robot/.env ] && cp ~/knml-robot/.env .env
# zapisana kalibracja radaru zostaje na robocie
[ -d ~/knml-robot/data ] && cp -a ~/knml-robot/data ./data
rm -rf ~/knml-robot.old
[ -d ~/knml-robot ] && mv ~/knml-robot ~/knml-robot.old
mv ~/knml-robot.new ~/knml-robot
echo "    pliki na miejscu: ~/knml-robot (poprzednia wersja: ~/knml-robot.old)"
REMOTE

say "Konfiguracja systemu (setup_rpi.sh)"
"${SSH[@]}" 'cd ~/knml-robot && sudo ./scripts/setup_rpi.sh'

say "Budowa i start (pierwszy raz kilka minut)"
"${SSH[@]}" 'cd ~/knml-robot && docker compose up -d --build --remove-orphans'

check() {
  "${SSH[@]}" 'bash -s' <<'REMOTE'
for i in $(seq 1 40); do
  if curl -sf http://localhost:8000/api/health >/dev/null; then break; fi
  sleep 3
done
cd ~/knml-robot
printf 'kontener: '; docker inspect -f '{{.State.Status}} / {{.State.Health.Status}} / restart={{.HostConfig.RestartPolicy.Name}}' knml-robot 2>/dev/null || echo "BRAK"
printf 'serwer:   '; curl -sf http://localhost:8000/api/health >/dev/null && echo "odpowiada" || echo "NIE ODPOWIADA"
printf 'kiosk:    '
if grep -Eqs '^[[:space:]]*KIOSK[[:space:]]*=[[:space:]]*0' .env; then echo "wyłączony (KIOSK=0): ekran z laptopa, http://lazik.local:8000"
elif pgrep -f -- '--kiosk' >/dev/null; then echo "Chromium działa"
else echo "nie działa (uruchomi się po zalogowaniu do pulpitu)"; fi
printf 'temperatura: '; vcgencmd measure_temp 2>/dev/null | cut -d= -f2 || echo "?"
# get_throttled: 0x0 = OK; bit 0/16 – za niskie napięcie, bit 2/18 – zwalnianie z powodu temperatury
thr=$(vcgencmd get_throttled 2>/dev/null | cut -d= -f2)
case "$thr" in
  0x0) echo "zasilanie i chłodzenie: OK (brak spowolnień)" ;;
  "") echo "zasilanie i chłodzenie: brak odczytu" ;;
  *) echo "UWAGA get_throttled=$thr – Pi zwalniało (za niskie napięcie z akumulatorów albo przegrzanie)" ;;
esac
echo "--- czujniki:"
docker compose exec -T robot python -m app.diag 2>/dev/null | tail -25 || true
REMOTE
}

say "Sprawdzenie"
check

if [ "$REBOOT_TEST" -eq 1 ]; then
  say "Test autostartu: restart Pi (jak po podłączeniu akumulatorów)"
  "${SSH[@]}" 'sudo systemctl reboot' || true
  sleep 20
  if ! wait_ssh 240; then
    echo "Pi nie wróciło do sieci w 4 minuty po restarcie." >&2
    exit 1
  fi
  sleep 25    # pulpit + kiosk startują chwilę po SSH
  say "Stan po restarcie"
  check
fi
say "Gotowe. Ekran: http://lazik.local:8000"
