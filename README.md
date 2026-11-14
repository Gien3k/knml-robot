# Człowiek kontra AI · Autonomiczny Łazik 4WD

Demo na stoisko Koła Naukowego Machine Learning PRz. Waveshare RaspRover (4WD, ESP32) stoi na
podstawce z kołami w powietrzu, obok duży monitor w trybie kiosku.

**Scenariusz: „Dłoń to gaz, LiDAR to hamulec”.**

| Co robi odwiedzający | Co robią koła | Co pokazuje ekran |
|---|---|---|
| nic (nikt nie podchodzi) | stoją; co ~20 s krótko „machają” | CZEKAM, zachęta w nagłówku |
| otwarta dłoń 35–120 cm od kamery | jadą; **bliżej = szybciej**, dłoń w bok = skręt | szkielet dłoni (21 punktów), odległość, gaz % |
| pięść | jadą do tyłu | WSTECZNY |
| ręka blisko robota (w „tarczy” LiDAR-u) | **natychmiast stop**, nawet gdy kamera mówi „jedź” | czerwony sektor na radarze, `Kamera: JEDŹ 72% · LiDAR: PRZESZKODA 0,18 m → HAMUJ` |

Fuzja sensorów widać na środkowej karcie: punkty LiDAR-u i pozycja dłoni z kamery 3D w jednym
układzie współrzędnych oraz pasek decyzji, w którym LiDAR ma prawo weta.

**Ludzie przy stoisku i refleks robota:**

- **Kamera** (OAK-D Pro) zaznacza każdą osobę w kadrze ramką z pewnością i odległością („osoba 92% · 1,4 m”).
- **Radar** (LiDAR) śledzi obiekty wokół robota, z numerem, śladem ruchu i licznikiem „3 osoby w pobliżu”.
- **Fuzja:** obiekt widziany przez oba czujniki staje się na mapie niebieską „osobą”. Ten widziany tylko przez radar zostaje szarym „obiektem”, bo LiDAR 2D nie odróżni człowieka od torby.
- **Refleks robota:** po każdym STOP karta napędu pokazuje czas reakcji hamulca („0,11 s”), rekord dnia i czas do całkowitego zatrzymania kół.

---

## Szybki start – laptop (symulacja)

```bash
docker compose up --build
```

Ekran: **http://localhost:8090**. Bez podłączonego sprzętu działa pełne demo w symulacji: wirtualny
odwiedzający podchodzi, steruje dłonią, zaciska pięść i wkłada drugą rękę w tarczę LiDAR-u.
Działa na Docker Desktop (Windows/macOS) i na Linuksie.

Klawisze (opcjonalne, kiosk działa bez klawiatury): `R` – ponowna nauka tła LiDAR-u, `M` – lustro obrazu,
`F` – pełny ekran.

## Raspberry Pi 5 – prawdziwy sprzęt

Raspberry Pi OS Bookworm 64-bit z pulpitem.

```bash
git clone <repo> ~/knml-robot && cd ~/knml-robot
curl -fsSL https://get.docker.com | sh                 # jeśli nie ma Dockera
sudo bash scripts/setup_rpi.sh                         # jednorazowo, idempotentny
sudo reboot
cd ~/knml-robot && docker compose up -d --build        # port 8000, autostart po restarcie
docker compose exec robot python -m app.diag           # czy sprzęt jest widoczny
```

`setup_rpi.sh` ustawia `COMPOSE_FILE=docker-compose.yml:docker-compose.rpi.yml` w `.env`, więc na RPi
zwykłe `docker compose …` zawsze używa nakładki. Bez skryptu:
`docker compose -f docker-compose.yml -f docker-compose.rpi.yml up -d --build`.

Co robi `setup_rpi.sh`: reguła udev dla OAK-D (03e7), `usb_max_current_enable=1` i `dtparam=uart0=on`
w `config.txt`, wyłącza konsolę szeregową na UART, maskuje ModemManager i brltty (potrafią przejąć lub
„sondować” porty USB-UART), wyłącza wygaszanie ekranu, dodaje autostart Chromium w trybie kiosku.

### Okablowanie i zasilanie

| Urządzenie | Podłączenie | Uwagi |
|---|---|---|
| OAK-D Pro | **niebieski port USB 3** | Z projektorem IR pobiera więcej, niż Pi daje przy zasilaniu z płytki robota. Bez `usb_max_current_enable=1` albo zasilanego huba / kabla Y kamera losowo się rozłącza, co wygląda jak błąd oprogramowania. |
| LiDAR D500 | USB (adapter z zestawu) | 230400 bodów, tylko nadaje – nigdy nic do niego nie wysyłamy |
| ESP32 RaspRover | UART na GPIO (`/dev/ttyAMA0`) albo USB-C płytki | 115200 bodów, JSON |

Kolejność wpinania kabli nie ma znaczenia. Każdy port jest rozpoznawany po treści danych.

## Kropki diagnostyczne (prawy dolny róg)

`OAK-D` · `LiDAR` · `ESP32`: **szara** = symulacja / nie wykryto, **szara pulsująca** = uruchamianie,
**zielona** = fizyczne urządzenie daje poprawne dane, **czerwona** = wykryte, ale błąd
(np. kamera się restartuje). Szczegóły: `http://<adres>:8000/api/devices`.

Pełne demo z symulowanymi zdarzeniami działa **tylko wtedy, gdy wszystkie trzy urządzenia są
symulowane**. Gdy choć jedno jest prawdziwe, brakujące pokazują „Czekam na czujnik” zamiast
fałszywych danych. Przełączanie w obie strony działa w locie: bez restartu kontenera i bez
odświeżania strony.

## Jak działa rozpoznawanie sprzętu

**Porty szeregowe** ([backend/app/hw/discovery.py](backend/app/hw/discovery.py)). Kandydaci to porty
z `pyserial.tools.list_ports` o znanych mostkach (CP2102 `10C4:EA60`, CH340 `1A86:7523/7522`,
CH343 `1A86:55D3`, CH9102 `1A86:55D4`, Espressif `303A:*`) oraz `/dev/ttyAMA0` i `/dev/serial0`.
VID:PID jest tylko wskazówką, bo LiDAR i ESP32 mogą mieć ten sam mostek. Dla każdego nieprzypisanego portu:

1. otwarcie bez resetu ESP32 (kolejność linii DTR/RTS opisana w [serial_util.py](backend/app/hw/serial_util.py)),
2. 230400 bodów, 400 ms nasłuchu: ≥ 3 ramki LD19 z poprawnym CRC8 → **LiDAR** (od tej chwili port jest tylko do odczytu),
3. 115200 bodów, do 3 s nasłuchu: linia JSON z kluczem `"T"` → **ESP32** (domyślnie sam nadaje telemetrię),
4. tylko gdy port milczał: jedno zapytanie `{"T":130}` i 500 ms na odpowiedź. Przy binarnym szumie nic nie jest wysyłane,
5. inaczej „unknown” i ponowna próba za 10 s.

Komenda ruchu wychodzi **wyłącznie** na port z potwierdzoną rolą ESP32. Wyjątek portu albo 2 s bez
poprawnych danych zwalnia port; urządzenie wraca do symulacji i port jest sondowany od nowa.

**OAK-D Pro** ([backend/app/hw/camera.py](backend/app/hw/camera.py)). Co 2 s obecność jest sprawdzana
przez sysfs (VID 03e7) i `dai.Device.getAllAvailableDevices()`. Kamera działa w **osobnym procesie**:
zawieszenie biblioteki natywnej nie blokuje serwera. Brak klatki przez 3 s albo wyjątek ubija proces,
a nowy startuje po 1 → 2 → 5 s. Śledzenie dłoni to
[geaxgx/depthai_hand_tracker](vendor/depthai_hand_tracker/VENDORED.md) (MIT, przypięty commit) w trybie
Edge: MediaPipe Hands na VPU, model „lite”, `xyz` z głębi stereo, gesty FIVE/FIST. Gdy model się nie
załaduje, działa **tryb zapasowy**: kolor + głębia stereo, a gazem steruje najbliższy obiekt w strefie
35–120 cm (w logu `!!! TRYB ZAPASOWY KAMERY`, na obrazie plakietka „TRYB ZAPASOWY”).

**Detekcja osób na kamerze** ([camera_worker.py](backend/app/hw/camera_worker.py)). MobileNet-SSD
skompilowany na 4 rdzenie SHAVE ([vendor/models/](vendor/models/README.md)) jest doklejony do potoku hand
trackera na tym samym VPU. Węzeł Script przepuszcza co 4. klatkę (`OAK_PERSON_EVERY_N`), więc śledzenie
dłoni zachowuje pierwszeństwo, a odległość osoby pochodzi z głębi stereo. Zabezpieczenia:
- jeśli VPU nie uruchomi potoku z detektorem, kamera od razu startuje bez niego, a nie w trybie zapasowym;
- jeśli kamera dwa razy padnie w ciągu minuty od startu z detektorem, detekcja osób wyłącza się do restartu kontenera.

Stan widać w `/api/devices` (`person_detect_*`) i w opisie kamery.

**Ludzie na mapie** ([logic/people.py](backend/app/logic/people.py)). Punkty LiDAR-u bliższe niż
nauczone tło są grupowane w obiekty i śledzone (numer, prędkość, ślad 2,5 s). „Osoba” to obiekt o
szerokości ≥ 15 cm, dalej niż 0,6 m od LiDAR-u (bliżej to ręka sięgająca do robota) albo potwierdzony
kamerą. Fuzja dopasowuje osoby z kamery (pozycja 3D) do obiektów radaru w promieniu 0,6 m. Mapa ma
skalę „rybie oko”: do 1 m liniowo, dalej ściśniętą, a pierścienie pokazują prawdziwe odległości.

**Czas reakcji hamulca** liczony jest od pierwszego skanu LiDAR-u z ręką w tarczy do chwili, gdy sterownik
ESP32 wysłał STOP (w symulacji do decyzji o stopie). Tarcza potwierdza wtargnięcie w 2 kolejnych obrotach,
więc przy 10 obr./s typowy wynik to ok. 0,1–0,15 s. „Koła stanęły po” pochodzi z telemetrii prędkości kół
ESP32, jeśli firmware ją podaje. Rekord dnia jest trzymany w pamięci i zeruje się o północy lub po restarcie.

**ESP32** ([backend/app/hw/esp32.py](backend/app/hw/esp32.py), stałe w
[protocol/waveshare.py](backend/app/protocol/waveshare.py)). Ramka `{"T":1,"L":..,"R":..}` jest
wysyłana 20 razy na sekundę zawsze, także z zerami. Rampa 0 → max w 0,6 s, hamowanie 2× szybciej.
STOP awaryjny to zero wysłane natychmiast i trzykrotnie. Dane z kamery starsze niż 300 ms oznaczają
cel 0. `SIGTERM`, `docker stop` i wyjątki wysyłają stop. Dodatkowo heartbeat firmware'u `{"T":136,"cmd":1000}`
zatrzyma koła, gdyby proces padł twardo.

**Kiedy prawdziwe koła mogą się ruszyć:** kamera jest prawdziwa i daje dane, a prawdziwy LiDAR daje
skany nie starsze niż 0,5 s z nauczonym tłem. Bez LiDAR-u koła stoją, chyba że ustawisz `DRIVE_WITHOUT_LIDAR=1`.
To bezpieczniejszy wariant, wybrany wobec niejasności w specyfikacji.

## Konfiguracja (`.env`)

Pełna lista z opisami: [.env.example](.env.example). Najważniejsze:

| Zmienna | Domyślnie | Opis |
|---|---|---|
| `HW_MODE` | `auto` | `auto` / `sim` (zawsze symulacja) / `real` (nigdy symulacja) |
| `PORT` | `8090` | port na laptopie (na RPi nakładka wystawia 8000) |
| `HAND_NEAR_M`, `HAND_FAR_M` | `0.35`, `1.20` | strefa sterowania dłonią [m od kamery] |
| `MIN_THROTTLE` | `0.25` | minimalny gaz w strefie |
| `ENABLE_REVERSE` | `1` | pięść = jazda do tyłu |
| `ATTRACT`, `ATTRACT_INTERVAL_S` | `1`, `20` | „przywołanie” kołami, gdy nikt nie podchodzi |
| `MAX_SPEED` | `0.5` | prędkość maksymalna w `{"T":1}` [m/s] |
| `RAMP_UP_S` | `0.6` | rozpędzanie 0 → max |
| `DRIVE_WITHOUT_LIDAR` | `0` | pozwól jechać bez LiDAR-u |
| `SHIELD_RADIUS` | `0.30` | promień tarczy od środka LiDAR-u [m] |
| `SHIELD_MARGIN_M` | `0.10` | o ile bliżej niż tło musi być przeszkoda |
| `SHIELD_LEARN_S` | `5` | nauka tła po starcie / po `R` |
| `LIDAR_STALE_S` | `0.5` | starszy skan = koła stoją |
| `LIDAR_ANGLE_OFFSET_DEG`, `LIDAR_CLOCKWISE` | `0`, `1` | montaż LiDAR-u względem przodu robota |
| `LIDAR_PORT`, `ESP32_PORT` | puste | wymuszenie portu (ESP32 i tak musi odpowiedzieć JSON-em) |
| `SERIAL_EXTRA_IDS`, `SERIAL_PROBE_ALL` | puste, `0` | dodatkowe mostki USB-UART / sondowanie wszystkich portów |
| `ESP32_INIT` | `{"T":131,"cmd":1};{"T":136,"cmd":1000}` | komendy konfiguracyjne po połączeniu (bez komend ruchu) |
| `OAK_IR_DOT` | `0.3` | projektor kropek IR 0..1 |
| `OAK_MIRROR` | `1` | lustrzany obraz |
| `OAK_FORCE_FALLBACK` | `0` | wymuś tryb zapasowy kamery |
| `OAK_PERSON_DETECT` | `1` | detekcja osób na kamerze (wyłącza się sama, gdy VPU nie daje rady) |
| `OAK_PERSON_EVERY_N` | `4` | detektor osób liczy co N-tą klatkę (~7 razy/s) |
| `OAK_PERSON_CONF` | `0.5` | minimalna pewność detekcji osoby |
| `PEOPLE_MAX_RANGE_M` | `4.0` | do jakiej odległości radar śledzi obiekty |
| `CAM_OFFSET_X/Y/Z` | `0.10`, `0`, `-0.05` | kamera względem środka LiDAR-u [m] – pozycja dłoni na mapie |

## Diagnostyka

```bash
docker compose exec robot python -m app.diag     # porty, role, ramki LD19, JSON, kamery OAK
docker compose logs -f robot                     # zdarzenia: wykrycia, rozłączenia, STOP
curl -s localhost:8000/api/devices | python3 -m json.tool
```

`app.diag` przy działającym serwerze pokazuje role już przypisane przez serwer (port zajęty = „busy”,
to dobrze). Komunikat DepthAI `USB protocol not available` oznacza, że kontener nie widzi
`/dev/bus/usb`: sprawdź, czy działa nakładka `docker-compose.rpi.yml`. W ostateczności można dopisać
`privileged: true` w nakładce.

## Checklista „10 minut przed imprezą”

1. **Zasilanie**: akumulator robota naładowany; OAK-D Pro w niebieskim porcie USB 3 (przez zasilany hub, jeśli kamera się rozłącza).
2. Robot na podstawce, **koła w powietrzu**, ok. 50 cm od krawędzi stołu (inaczej brzuch stojącej osoby wejdzie w tarczę).
3. Włącz RPi i poczekaj na ekran kiosku (~1 min).
4. **Trzy zielone kropki** w prawym dolnym rogu. Szara: kabel / `app.diag`. Czerwona: `docker compose logs robot`.
5. Odsuń się od robota na 5 s, aż radar nauczy się tła (napis „UCZĘ SIĘ”). Po przestawieniu czegoś na stole naciśnij `R` albo wyślij `curl -X POST localhost:8000/api/recalibrate`.
6. Test: otwarta dłoń ok. 60 cm przed kamerą → JAZDA, koła się kręcą; zbliż dłoń → szybciej; przesuń w bok → różne prędkości stron.
7. Test: pięść → WSTECZNY.
8. **Test hamulca**: trzymaj otwartą dłoń przed kamerą i drugą ręką sięgnij blisko robota z boku → STOP, czerwony sektor, „HAMUJ”, czas reakcji w karcie napędu. Zabierz rękę → po 0,5 s koła ruszają.
9. Stań 1–2 m przed robotem: ramka „osoba” na obrazie i niebieska „osoba” na mapie. Jeśli ramek brak, sprawdź w `/api/devices`, czy detekcja osób się nie wyłączyła (`person_detect_active`).
10. Odłącz kamerę na chwilę → koła stają, kropka szara/czerwona, po wpięciu wraca sama (do ~10 s).
11. Wyłącznik zasilania silników w zasięgu ręki obsługi.

## Testy

```bash
docker compose run --rm robot python -m pytest
```

Testy działają bez sprzętu, w kontenerze Linux, bo testy portów wymagają pty:

- parser LD19 na syntetycznych ramkach; tablica CRC8 porównana z oficjalnym sterownikiem LDROBOT,
- auto-detekcja na wirtualnych portach: fałszywy LiDAR i fałszywy ESP32 w obu kolejnościach,
  ESP32 milczący do zapytania `{"T":130}`, binarny szum, port zajęty; **do portu LiDAR-u nie trafia ani
  jeden bajt**, a ESP32 nie dostaje komendy ruchu przed potwierdzeniem roli,
- sterownik ESP32: ~20 komend/s, zera bez zgody, tarcza i STOP awaryjny (3 × zero w < 100 ms), stop przy zamknięciu, zwolnienie milczącego ESP32,
- wypięcie LiDAR-u → zwolnienie portu i powrót do symulacji,
- logika: rampa, hamowanie 2×, watchdog percepcji, priorytet tarczy, gesty, skręt, przywołanie,
- tarcza: nauka tła, 2 kolejne obroty, histereza i zwolnienie po 0,5 s,
- OAK-D bez urządzenia: prawdziwy potok DepthAI 2.30 z dołączonego hand trackera (bloby, skrypt na VPU)
  oraz ścieżka host → proces kamery → dłoń na atrapie urządzenia; potok trybu zapasowego,
- detekcja osób doklejona do potoku hand trackera (węzły, głębia 3D, filtr klasy „osoba”) i automatyczny
  start bez niej, gdy VPU odrzuci potok,
- ludzie z LiDAR-u: grupowanie (także przez 359°/0°), śledzenie z numerem i śladem, fuzja z kamerą;
  w symulacji kamera i radar wskazują tę samą osobę,
- czas reakcji hamulca: symulacja, czekanie na potwierdzenie wysłania stopu przez ESP32, rekord dnia,
- zasady ruchu (prawdziwy ESP32 + symulowana kamera = brak ruchu, stary LiDAR = stop), serwer w symulacji
  (strona bez zasobów z internetu, WebSocket, skany, logotypy).

## Architektura

```
OAK-D Pro --USB3--> [proces kamery] hand tracker na VPU -> dłoń (21 pkt, gest, xyz) + JPEG --+
LiDAR D500 --UART--> [lidar] ramki LD19 -> obrót 360° -> tarcza (tło, wtargnięcie)        --+--> Hub
ESP32 <--UART JSON-- [esp32] {"T":1} 20 Hz, stop awaryjny, telemetria  <--- [sterowanie 20 Hz] <-+
                                                                            |
              FastAPI: /ws (stan 20 Hz + skany), /stream.mjpg  --> Chromium --kiosk (HTML/CSS/JS)
```

| Ścieżka | Zawartość |
|---|---|
| [backend/app/logic/](backend/app/logic/) | czysta logika: maszyna stanów napędu, tarcza LiDAR, zapasowy detektor dłoni |
| [backend/app/hw/](backend/app/hw/) | sprzęt: auto-detekcja portów, LiDAR, ESP32, kamera (nadzorca + proces) |
| [backend/app/protocol/](backend/app/protocol/) | protokoły LD19 i Waveshare JSON (wszystkie stałe) |
| [backend/app/sim/](backend/app/sim/) | symulacja: świat stoiska, render podczerwieni, syntetyczna dłoń 21 pkt |
| [backend/app/supervisor.py](backend/app/supervisor.py) | hot-plug, przypisanie ról portów, włączanie / wyłączanie symulacji |
| [backend/app/control.py](backend/app/control.py) | pętla 20 Hz, zasady dopuszczenia ruchu, teksty dla odwiedzających |
| [frontend/](frontend/) | ekran kiosku: czysty HTML/CSS/JS + canvas, fonty lokalnie (Barlow Condensed, Inter) |
| [vendor/depthai_hand_tracker/](vendor/depthai_hand_tracker/) | kopia hand trackera (MIT) z modelami `.blob` |
| [vendor/models/](vendor/models/) | detektor osób MobileNet-SSD (4 SHAVE) |

API: `GET /api/health`, `/api/state`, `/api/config`, `/api/devices`; `POST /api/recalibrate`;
`WS /ws`; `GET /stream.mjpg`; `GET /branding/{kolo,rzeszow}`.

## Logotypy

`assets/logo_kola.(svg|png)` i `assets/logo_rzeszow.(svg|png)`, wersje jasne na ciemne tło. Folder jest
montowany na żywo, a podmiana pojawia się na ekranie w ciągu ~30 s. Brak pliku → biały placeholder SVG.

## Bezpieczeństwo i ograniczenia

- LiDAR 2D skanuje płaszczyznę na wysokości swojej głowicy, nad kołami. Wykryje rękę sięgającą do
  robota, ale **nie palce przy samych kołach**. Wyłącznik silników ma być w zasięgu obsługi.
- Tarcza uczy się tła po starcie. Jeśli ktoś stał przy robocie w tym czasie, naciśnij `R` / `POST /api/recalibrate`.
- Kierunek skrętu, `LIDAR_ANGLE_OFFSET_DEG` i `CAM_OFFSET_*` zależą od montażu – sprawdź na sprzęcie
  (punkt 6 i 8 checklisty).
