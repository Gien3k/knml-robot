# KN ML · ADAS 4WD – interaktywny symulator ADAS z fuzją sensorów

Projekt pokazowy na stoisko Koła Naukowego Machine Learning. Robot Waveshare RaspRover (4WD, ESP32)
reaguje na gesty odwiedzających (OAK-D Pro) i zatrzymuje się, gdy ktoś za bardzo się zbliży (LiDAR D500).
Dashboard w trybie kiosku pokazuje wszystko na żywo na dużym monitorze.

| Sensor | Rola |
|---|---|
| **OAK-D Pro** | detekcja osób z pozycją 3D (MobileNet-SSD na VPU), głębia stereo z projektorem IR, gesty dłoni |
| **LiDAR D500** | radar 360°, śledzenie osób, strefy bezpieczeństwa, wirtualna klatka (ICP) |
| **Kamera FPV** | widok z robota, głowica pan-tilt śledzi najbliższą osobę |
| **ESP32 (4WD)** | napęd skid-steer (lewa/prawa strona), telemetria, sterowanie głowicą |

**Gesty:** otwarta dłoń = jazda, pięść = stop, przesunięcie dłoni w bok = skręt.
**Priorytety bezpieczeństwa:** STOP operatora > STOP AWARYJNY LiDAR (< 12 cm) > blokada kierunku (< 25 cm) + odbicie > wirtualna klatka > gest.

---

## Szybki start (dowolny komputer z Dockerem)

```bash
git clone <repo> knml-robot && cd knml-robot
docker compose up --build
```

Dashboard: **http://localhost:8000**

Bez podłączonego sprzętu wszystko działa od razu w **trybie symulacji**. Wspólny wirtualny świat stoiska
zawiera ludzi, „kierowcę” wykonującego gesty i dłoń wkładaną pod robota. Każdy sensor jest wykrywany osobno
co 2 s: po podłączeniu kabla prawdziwe dane same zastępują symulację, a po odłączeniu wraca symulacja
(hot-plug, bez restartu kontenera).

Port zajęty? Skopiuj `.env.example` do `.env` i ustaw np. `PORT=8090`.

## Konfiguracja (`.env`)

Najważniejsze zmienne (pełna lista w [.env.example](.env.example)):

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `HW_MODE` | `auto` | `auto` – sprzęt + symulacja brakujących, `mock` – tylko symulacja, `real` – tylko sprzęt |
| `MOTION_MODE` | *(puste)* | `stand` – podstawka, koła w powietrzu · `mat` – ruchy na macie z wirtualną klatką · puste: `mat` w symulacji, `stand` z prawdziwym ESP32 |
| `SAFETY_DISTANCE_M` | `0.25` | blokada ruchu w kierunku przeszkody (od obrysu robota) |
| `ESTOP_DISTANCE_M` | `0.12` | stop awaryjny |
| `GEOFENCE_RADIUS_M` | `0.20` | promień wirtualnej klatki |
| `MAX_WHEEL_SPEED` / `MAT_MAX_WHEEL_SPEED` | `0.30` / `0.10` | limity prędkości kół [m/s] |
| `LIDAR_PORT`, `ROVER_PORT`, `FPV_DEVICE` | auto | wymuszenie portów, gdy autodetekcja nie wystarcza |

## Raspberry Pi 5 (docelowo)

```bash
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER   # potem wyloguj/zaloguj
sudo raspi-config nonint do_serial_hw 0      # UART na GPIO dla ESP32 (/dev/ttyAMA0)
sudo raspi-config nonint do_serial_cons 1    # bez konsoli na UART
echo "usb_max_current_enable=1" | sudo tee -a /boot/firmware/config.txt  # pełny prąd USB dla OAK-D
sudo raspi-config nonint do_blanking 1       # monitor nie gaśnie
sudo reboot
cd knml-robot && docker compose up -d --build
./scripts/kiosk.sh                            # pełny ekran na monitorze
```

Autostart kiosku (labwc): `echo "$HOME/knml-robot/scripts/kiosk.sh &" >> ~/.config/labwc/autostart`.
Kontener ma `restart: unless-stopped`, więc wstaje sam po restarcie RPi.

Uwagi sprzętowe:
- OAK-D Pro podłącz do **niebieskiego portu USB 3**; z projektorem IR pobiera dużo prądu, więc najlepiej przez aktywny hub lub Y-adapter Luxonis.
- Na RaspRover ESP32 jest podłączony do RPi przez UART na GPIO (`/dev/ttyAMA0`), a do laptopa przez USB-C płytki sterownika.
- LiDAR i ESP32 są rozpoznawane po **protokole**, nie po VID/PID (często mają ten sam konwerter CP2102/CH340).

## Laptop z Windows

Docker Desktop działa od ręki w trybie symulacji. Do testów sprzętu na Windows urządzenia USB trzeba
przekazać do WSL2 przez [usbipd-win](https://github.com/dorssel/usbipd-win)
(`usbipd bind` + `usbipd attach --wsl`). Prostsza i docelowa droga to sprzęt podpięty do RPi 5 albo laptop z Linuxem.

## Obsługa na stoisku (klawiatura)

| Klawisz | Akcja |
|---|---|
| `Spacja` | STOP operatora / wznowienie |
| `R` | ponowna kalibracja tła LiDAR i wirtualnej klatki |
| `V` | radar: widok od strony widza / od strony robota |
| `M` | lustro obrazu OAK |
| `D` | panel diagnostyczny |
| `F` / `H` | pełny ekran / pomoc |

## API

| Endpoint | Opis |
|---|---|
| `GET /api/health`, `GET /api/state`, `GET /api/config` | stan systemu |
| `GET /api/devices` | diagnostyka: co widzi kontener (USB, porty, kamery, DepthAI, sterowniki) |
| `POST /api/estop` `{"active": true}` | STOP operatora (bez treści = przełącz) |
| `POST /api/recalibrate` | kalibracja tła LiDAR / klatki |
| `POST /api/drive/test` `{"left":0.1,"right":0.1,"duration":2}` | test silników (zabezpieczenia nadal działają) |
| `WS /ws/telemetry` | stan 20 Hz + skany LiDAR 10 Hz (JSON) |
| `WS /ws/video/{oak,depth,fpv}` | binarnie: `[u32 długość][nagłówek JSON][JPEG]`, tylko najnowsza klatka |

## Logotypy

Wrzuć `logo_kola.svg|png` i `logo_rzeszow.svg|png` do [assets/](assets/). Folder jest montowany na żywo,
a dashboard podmieni logotypy w ciągu ~20 s bez przebudowy. Najlepiej sprawdzają się wersje jasne (negatyw) na ciemne tło.

## Architektura

```
OAK-D Pro  --USB3--> [oak]    detekcja 3D na VPU, MJPEG z VPU, głębia -> gesty  --+
LiDAR D500 --UART--> [lidar]  pakiety 0x54, tło, ICP, tory, strefy             --+--> Hub (najnowszy stan)
Kamera     --USB---> [fpv]    MJPEG bez dekodowania                          --+        |
ESP32 <--UART JSON-- [rover]  {"T":1,...} 20 Hz, głowica {"T":133}, telemetria <--- [ADAS 20 Hz] <-+
                                                                                       |
                     FastAPI: /ws/telemetry, /ws/video/*  --> Chromium --kiosk (Vite + TS + Tailwind)
```

- [backend/app/drivers/](backend/app/drivers/) – sterowniki (prawdziwe + symulowane), autodetekcja
- [backend/app/adas/](backend/app/adas/) – gesty z głębi, przetwarzanie LiDAR (ICP, tory, strefy), kontroler ADAS
- [backend/app/sim/world.py](backend/app/sim/world.py) – wspólny świat symulacji
- [backend/app/supervisor.py](backend/app/supervisor.py) – hot-plug i fallback do symulacji
- [frontend/src/](frontend/src/) – dashboard (scena 1920×1080 skalowana do monitora)

Rozwój frontendu z hot-reload: `cd frontend && npm install && npm run dev` (proxy do backendu na :8000).

## Bezpieczeństwo – ograniczenia, o których trzeba pamiętać

- **LiDAR 2D skanuje poziomą płaszczyznę na wysokości robota i nie widzi krawędzi stołu.** Wirtualna klatka
  (tryb `mat`) opiera się na lokalizacji ICP względem otoczenia stoiska. W trybie maty zawsze używaj fizycznego
  ograniczenia (mata z rantem). Bezpieczny domyślny tryb dla prawdziwego robota to `stand` (podstawka).
- Płaszczyzna LiDAR-u jest nad kołami. Wykrywa ręce sięgające do robota, ale nie palce przy samych kołach na
  podstawce, więc trzymaj limit prędkości kół i fizyczny wyłącznik silników w zasięgu obsługi.
- Prawdziwe koła **nie słuchają symulowanych sensorów**. Bez prawdziwej OAK-D napęd jest zablokowany, a tryb maty wymaga prawdziwego LiDAR-u.
