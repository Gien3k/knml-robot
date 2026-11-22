# Człowiek kontra AI · Autonomiczny Łazik 4WD

Demo na stoisko Koła Naukowego Machine Learning PRz. Waveshare RaspRover (4WD, ESP32) stoi na
podstawce z kołami w powietrzu, obok duży monitor w trybie kiosku.

**Scenariusz: „Dłoń to gaz, LiDAR to hamulec”.**

**Ekran startowy:** odwiedzający wybiera grę dłonią – bez dotykania ekranu. Kursorem jest jego dłoń
widziana przez kamerę: najeżdża nią na kartę **„Jedź robotem”** albo **„Papier, kamień, nożyce”** i przytrzymuje
(~1,3 s, napełnia się pasek). Działa też dotyk / kliknięcie karty, klawisze `1` / `2` (obsługa, `0` = powrót)
i kciuk w górę (od razu gra). Z jazdy robot wraca do menu po `DRIVE_IDLE_S` s bez dłoni, z gry – po finale.
W menu koła stoją. `MENU=0` = stary tryb (jazda dłonią i gra kciukiem bez wyboru).
Każda karta ma „nagrany” podgląd (animacja w pętli, rysowana na żywo – ostra na każdym monitorze):
jazda – dłoń zbliża się do kamery i koła kręcą się coraz szybciej, pięść = do tyłu;
gra – dłoń gracza i hologram robota rozgrywają trzy rundy (odliczanie, odsłona, wynik, licznik).

| Co robi odwiedzający | Co robią koła | Co pokazuje ekran |
|---|---|---|
| nic (nikt nie steruje) | stoją | menu na tle obrazu, który co kilka sekund zmienia się w **„Tak widzi Cię robot”** (mapa głębi) |
| otwarta dłoń 35–120 cm od kamery | jadą prosto; **bliżej = szybciej** | szkielet dłoni (21 punktów), „Jadę 60%” |
| pięść | jadą do tyłu | „Cofam” |
| **kciuk w górę** | „pompują” w rytm odliczania, tańczą po wygranej robota | **papier, kamień, nożyce** – 3 rundy, karty „Ty / Robot”, wynik |
| ręka blisko robota (w „tarczy” LiDAR-u) | **natychmiast stop**, także w grze | czerwona ramka, „Stop w 0,08 s” |

Fuzja sensorów widać na środkowej karcie: punkty LiDAR-u i pozycja dłoni z kamery 3D w jednym
układzie współrzędnych oraz pasek decyzji, w którym LiDAR ma prawo weta.

**Ludzie przy stoisku i refleks robota:**

- **Kamera** (OAK-D Pro) zaznacza każdą osobę w kadrze ramką z pewnością i odległością („osoba 92% · 1,4 m”).
- **Radar** (LiDAR) śledzi obiekty wokół robota, z numerem, śladem ruchu i licznikiem „3 osoby w pobliżu”.
- **Fuzja:** obiekt widziany przez oba czujniki staje się na mapie niebieską „osobą”. Ten widziany tylko przez radar zostaje szarym „obiektem”, bo LiDAR 2D nie odróżni człowieka od torby.
- **Refleks robota:** po każdym STOP karta napędu pokazuje czas reakcji hamulca („0,11 s”), rekord dnia i czas do całkowitego zatrzymania kół.

**Papier, kamień, nożyce („Człowiek kontra AI”):**

- Start: kciuk w górę przytrzymany przez pół sekundy (wokół dłoni napełnia się pierścień) albo klawisz `G` / `POST /api/game/start`.
- Ekran dzieli się po skosie. Z lewej jest odwiedzający na żywo, z prawej **dłoń robota** – hologram 21 punktów, który naprawdę gra:
  - zaciska pięść palec po palcu i pompuje nią w rytm „Papier – Kamień – Nożyce – Teraz!” (prawdziwe koła w tym samym rytmie);
  - na „Teraz!” rzuca ręką i otwiera palce po kolei w swój ruch;
  - po wygranej tańczy nadgarstkiem, a po przegranej opada.
- Gdy w kadrze są dwie dłonie, steruje ta **bliżej kamery** (większa); kamera trzyma się jej, dopóki jej nie zgubi.
  Dłoń opuszczona na kolanach nie przejmuje kursora ani gry. Lewo- i praworęczni grają tak samo.
- Gest rozpoznaje [backend/app/logic/rps.py](backend/app/logic/rps.py) z 21 punktów dłoni. Liczą się cztery palce, bez kciuka.
  Dłoń jest dopasowywana do wzorca jako całość, więc jeden palec zgięty w połowie (rozluźniona dłoń) nie psuje gestu.
  Od „Teraz!” klatki głosują (nie muszą iść jedna po drugiej), a gdy czas mija, wygrywa gest widziany najczęściej.
  `http://<adres>:8000/api/game/debug` pokazuje ostatnie klatki z „Teraz!”: gest i wyprostowanie każdego palca (0..1).
  Przy odsłonięciu ekran zamraża klatkę z dłonią odwiedzającego.
- Przed meczem jest **rozgrzewka** bez punktów: ten sam rytm „Papier, kamień, nożyce, TERAZ!” i podpowiedź,
  że gest pokazuje się dopiero na „TERAZ!”. Po niej „DOBRZE!” i runda 1. Bez gestu: przypomnienie i jeszcze raz,
  za drugim razem mecz rusza bez rozgrzewki.
- Gramy **do 3 wygranych** (best of 5, remisy się nie liczą; bezpiecznik: najwyżej 9 rund). Rundy są **uczciwe**:
  robot losuje ruch, zanim zobaczy dłoń. Tylko w **rundzie decydującej przy 2:2** robot oszukuje: patrzy na gest
  i wybiera ruch, który wygrywa (pigułka „RUNDA DECYDUJĄCA”). Po wyniku przyznaje się i pokazuje
  **powtórkę w zwolnionym tempie ×4** z nagranych klatek. Przy 3:0 czy 3:1 nie ma oszukiwania ani powtórki.
- Odliczanie idzie w rytmie 0,8 s na słowo. Dłoń robota i koła pompują w tym samym rytmie, który podaje serwer.
- Gdy na „Teraz!” nie widać gestu: najpierw „NIE WIDZĘ GESTU” z trzema gestami do wyboru i powtórka rundy.
  Za drugim razem w tej samej rundzie robot wygrywa ją walkowerem („BRAK GESTU”), a mecz toczy się dalej.
  Gdy dłoni w ogóle nie widać przez 7 s, gra się kończy.
- Wynik rundy to „PUNKT DLA CIEBIE” / „PUNKT DLA ROBOTA” / „REMIS”, a wynik meczu „WYGRYWASZ” /
  „ROBOT WYGRYWA” / „REMIS”, żeby jedno nie myliło się z drugim.
- Po powtórce wynik wraca z dwoma przyciskami: **Spróbuj ponownie** (po wygranej **Zagraj jeszcze raz**) i **Menu**.
  Wybiera się je dłonią, przytrzymując ją nad przyciskiem jak nad kartą, albo dotykiem. Obraz z kamery wraca wtedy
  na środek, a dłoń trzymana na wprost trafia w przerwę między przyciskami, więc nic nie wybiera się samo.
  Gdy dłoni nie widać przez 5 s, ekran wraca do menu, najpóźniej po 25 s.
- Wynik pojawia się też na ekraniku OLED łazika (`{"T":3}`), a po grze robot przywraca ekran fabryczny (`{"T":-3}`).
- Bez dźwięku i bez efektów na tle: ruch ma przede wszystkim dłoń robota. Na Raspberry animowane są tylko transformacje i przezroczystość.

**Jak widzi robot (głębia):**

- Przełącznik **Kolor · Głębia** jest w stopce z lewej, na każdym ekranie (start, jazda, gra).
  Działa dotykiem / myszą albo klawiszem `L`. Wybrany widok zostaje przez całą grę lub jazdę i jeszcze 25 s po niej,
  a gdy nikt nie steruje, sceny zmieniają się same. Zamrożona klatka i powtórka w grze są w tym samym widoku.
- **Głębia:** mapa odległości ze stereo OAK-D Pro, wyrównana do obrazu kolorowego. Im jaśniej, tym bliżej.
  Pod hasłem jest legenda w metrach, a nad ludźmi ich odległość.
- Na VPU kamery działa „bramka” (skrypt DepthAI). Do Raspberry wysyła głębię **tylko wtedy, gdy ekran
  ją pokazuje**, i tylko co N-tą klatkę, więc łącze USB zostaje dla obrazu i śledzenia dłoni.
  Gdy VPU nie zmieści bramki w potoku, kamera startuje bez niej, a przełącznik znika.

---

## Szybki start – laptop (symulacja)

```bash
docker compose up --build
```

Ekran: **http://localhost:8090**. Bez podłączonego sprzętu działa pełne demo w symulacji: wirtualny
odwiedzający podchodzi, steruje dłonią, zaciska pięść i wkłada drugą rękę w tarczę LiDAR-u.
Działa na Docker Desktop (Windows/macOS) i na Linuksie.

Klawisze (opcjonalne, kiosk działa bez klawiatury): `Shift+R` – ponowna nauka tła LiDAR-u,
`Shift+M` – lustro obrazu (dla wszystkich otwartych ekranów naraz),
`F` – pełny ekran, `1` – jazda, `2` – gra, `0` – ekran startowy, `G` – start / koniec gry,
`L` – następny widok (kolor → głębia → automat).

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

**Ekran z laptopa zamiast monitora przy robocie (zalecane, gdy do Raspberry nic nie jest podpięte):**
w `.env` robota ustaw `KIOSK=0`. Przeglądarka na Raspberry się wtedy nie uruchamia, a stronę otwiera laptop
pod `http://lazik.local:8000` (F11 = pełny ekran; duży monitor podpinasz do laptopa). Gra, kursor dłonią
i sterowanie dalej działają na robocie. Raspberry jest chłodniejsze, bo rysowanie strony brało ok. ¼ procesora.
Na stoisku najpewniej połączyć laptop z robotem kablem sieciowym albo hotspotem na 5 GHz (Wi‑Fi 2,4 GHz w tłumie bywa zapchane).

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

Obok zawsze jest temperatura procesora, np. `RPi 54°C`. Kropka jest **zielona** do 70°C,
**żółta** do 80°C, a od 80°C **czerwona** (Raspberry samo wtedy zwalnia). W symulacji temperatura
jest udawana, tak jak napięcie akumulatorów.

Kilka otwartych stron naraz (kiosk, laptop, telefon obsługi) nie przeszkadza sobie: lustro obrazu jest wspólne,
a wybór dłonią przychodzi z numerem ekranu, więc spóźniony wybór z innej strony serwer pomija.
`http://<adres>:8000/api/clients` pokazuje otwarte strony i to, czy mają aktualną wersję ekranu.

Po wgraniu nowej wersji otwarta strona kiosku przeładuje się sama, gdy tylko nikt nie gra.
Serwer podaje odcisk plików ekranu, a strona porównuje go po każdym połączeniu. Nie trzeba
naciskać F5.

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
| `RAMP_UP_S`, `RAMP_DOWN_S` | `1.6`, `0.8` | łagodny napęd: rozpędzanie 0 → max i zwykłe zatrzymanie [s] (ochrona silników) |
| `SMOOTH_S`, `REVERSE_PAUSE_S`, `STOP_FAST_S` | `0.2`, `0.3`, `0.3` | miękki start, postój przed zmianą kierunku, szybkie zatrzymanie po utracie obrazu (STOP radaru zawsze natychmiast) |
| `SHIELD_ABSORB_S`, `SHIELD_FORGET_S` | `45`, `5` | nieruchomy przedmiot przy robocie -> tło (0 = wyłączone); zabrany przedmiot -> tło wraca |
| `PLAYER_MAX_DIST_M` | `1.7` | dalsze dłonie (przechodnie) nie sterują |
| `BATTERY_LOW_V`, `BATTERY_CRITICAL_V` | `10.8`, `10.2` | ostrzeżenie w stopce / koła stoją (3 ogniwa 18650) |
| `OAK_WORLD_LANDMARKS` | `1` | punkty dłoni 3D: gest z kątów w stawach + głosowanie z kilku klatek (jazda i gra) |
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
| `OAK_IR_ALWAYS` | `0` | projektor świeci tylko przy widoku „Głębia” (mniej ciepła kamery); `1` = stale |
| `OAK_VISION_EVERY_N` | `3` | co która klatka głębi idzie na ekran |
| `VISION` | `1` | widok „Głębia” (0 = tylko obraz kolorowy) |
| `VISION_SCENES` | `drive:10,depth:12` | sceny, gdy nikt nie steruje: nazwa:sekundy, w tej kolejności (`game` = zaproszenie do gry, gdy `MENU=0`) |
| `MENU` | `1` | ekran startowy z wyborem gry (0 = jazda i gra dostępne naraz) |
| `DRIVE_IDLE_S` | `8` | po tylu sekundach bez dłoni jazda wraca do ekranu startowego |
| `VISION_WIDTH` | `960` | szerokość obrazu głębi [px] |
| `RPS` | `1` | gra papier, kamień, nożyce (start kciukiem w górę) |
| `RPS_WINS`, `RPS_ROUNDS` | `3`, `9` | gra do tylu wygranych (best of 5) i limit rund na wypadek remisów; `RPS_WINS=0` = stała liczba rund |
| `RPS_CHEAT_LAST` | `1` | w rundzie decydującej (2:2) robot „podgląda” gest i się przyznaje |
| `RPS_WARMUP` | `1` | przed rundą 1 rozgrzewka bez punktów: „Pokaż gest dopiero na TERAZ!” |
| `RPS_WHEELS`, `RPS_WHEEL_SPEED` | `0`, `0.15` | koła w grze (rytm, taniec) [m/s]; domyślnie w grze koła stoją |
| `OLED` | `1` | wynik gry na ekraniku łazika |
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

## Stoisko w tłumie: co może pójść nie tak

**Zabezpieczone w programie:**

| Sytuacja | Co robi robot |
|---|---|
| Ktoś postawi kubek / torbę / telefon tuż przy robocie | Najpierw stop (może to ręka). Gdy przedmiot leży bez ruchu `SHIELD_ABSORB_S` s (koła stoją), staje się częścią tła. Po zabraniu tło wraca po `SHIELD_FORGET_S` s |
| Ktoś przesunie lub obróci robota | Radar widzi, że naraz dużo punktów jest bliżej i dalej niż tło: koła stają, radar sam uczy się stoiska od nowa |
| Ktoś sięga po coś obok stojącego robota | Koła i tak stoją, ale bez czerwonej ramki i „Stop w 0,1 s” (to tylko w jeździe, gdy koła się kręcą) |
| Przechodnie za graczem, dużo rąk w kadrze | Liczą się tylko dłonie bliżej niż `PLAYER_MAX_DIST_M` (1,7 m); gest z głosowania kilku klatek |
| Tłum przy robocie | Na radarze podpis mają 3 najbliższe osoby (podpisy nigdy się nie nakładają), na obrazie ramki 5 najbliższych |
| Akumulatory słabną | Poniżej `BATTERY_CRITICAL_V` koła stają (Raspberry nie straci zasilania od ruszających silników); napięcie widać w `/api/state` (`battery`) |
| Raspberry się grzeje | W stopce zawsze „RPi 54°C”: kropka żółta od 70°C, czerwona od 80°C |
| Cały dzień pracy przeglądarki | Co 4 h strona sama się odświeża, ale tylko w menu, gdy od minuty nikt nie gra |
| Przypadkowa klawiatura | Nauka tła radaru i lustro tylko z Shiftem (`Shift+R`, `Shift+M`) |
| Wypięty kabel USB | Automatyczne ponowne połączenie (kamera, LiDAR, ESP32), w tym czasie koła stoją |

**Do zrobienia na miejscu (tego program nie załatwi):**

1. Robot przymocowany do podnośnika (rzep / opaska), podnośnik do stołu: koła nie mogą dotknąć blatu.
2. Wokół robota ok. 40 cm wolnego miejsca, najlepiej zaznaczone taśmą. Nic wyższego niż LiDAR tuż obok (radar widzi tylko swoją płaszczyznę).
3. Między robotem a odwiedzającymi nic nie zasłania kamery ani radaru (laptop, roll-up, ulotki na stojaku).
4. Po ustawieniu stołu kalibracja radaru: 5× logo Koła, hasło `KNML`. To samo po przestawieniu robota.
5. Ładowarka łazika podłączona cały dzień (moduł UPS ładuje i zasila jednocześnie).
6. Kamera i kable przymocowane (opaski), OAK-D Pro w niebieskim porcie USB 3, najlepiej z kablem Y i zasilaczem.
7. Wentylator na Raspberry.
8. Bez klawiatury i myszy w zasięgu odwiedzających.
9. Za odwiedzającymi nie powinno być mocnego światła (okno, reflektor): pod światło kamera gorzej widzi dłoń.
10. Radar widzi tylko w swojej płaszczyźnie: ręka dziecka sięgająca do kół od dołu może zostać niezauważona. Na stoisko z dziećmi warto zmniejszyć `MAX_SPEED` (np. 0.3) i zostawić łagodny start.

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
| [backend/app/logic/](backend/app/logic/) | czysta logika: maszyna stanów napędu, tarcza LiDAR, gra (`rps.py`), głębia / podczerwień i sceny ekranu (`vision.py`), zapasowy detektor dłoni |
| [backend/app/hw/](backend/app/hw/) | sprzęt: auto-detekcja portów, LiDAR, ESP32, kamera (nadzorca + proces) |
| [backend/app/protocol/](backend/app/protocol/) | protokoły LD19 i Waveshare JSON (wszystkie stałe) |
| [backend/app/sim/](backend/app/sim/) | symulacja: świat stoiska, render podczerwieni, syntetyczna dłoń 21 pkt |
| [backend/app/supervisor.py](backend/app/supervisor.py) | hot-plug, przypisanie ról portów, włączanie / wyłączanie symulacji |
| [backend/app/control.py](backend/app/control.py) | pętla 20 Hz, zasady dopuszczenia ruchu, teksty dla odwiedzających |
| [frontend/](frontend/) | ekran kiosku: czysty HTML/CSS/JS + canvas, fonty lokalnie (Barlow, Barlow Condensed); gra w `game.js` |
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
