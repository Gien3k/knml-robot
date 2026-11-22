# Stan projektu i znane problemy (2026-11-14, uzupełnione 2026-11-22)

Notatka do dalszej pracy. Instrukcja uruchomienia i opis architektury: [README.md](README.md).

## 2026-11-22: porządek na ekranie gry

- Napisy z poprzedniej fazy nie mogą już zostać na ekranie. Arena 20 razy na sekundę pilnuje, co wolno
  pokazać w danej fazie (`ALLOWED` w `frontend/game.js`), a nowy mecz zaczyna się od czystej sceny.
  Wcześniejsze zrzuty z nakładającymi się napisami (wynik rundy pod planszą końcową i pod powtórką)
  pochodziły z karty podglądu nieodświeżonej po zmianach. Teraz strona sama się przeładowuje, gdy
  serwer ma nową wersję plików (`build` w `/api/config`).
- Temperatura Raspberry zawsze w prawym dolnym rogu (`RPi 54°C`), w symulacji udawana.
- Po meczu przyciski „Spróbuj ponownie” / „Menu” (`FinalChoice` w `frontend/menu.js`). Faza `final` trwa,
  dopóki gracz nie wybierze, najwyżej `final_s + choice_s`, albo kończy się po `choice_idle_s` bez dłoni.
- Symulacja: strona, w którą symulowany gracz przesuwa dłoń, zależy od `OAK_MIRROR`. Wcześniej przy domyślnym
  lustrze wybierał na ekranie startowym odwrotne karty. Do sprawdzenia na łaziku: czy przy prawdziwej kamerze
  kursor w menu i przy przyciskach po meczu idzie w tę samą stronę co dłoń.
- Rozgrzewka przed meczem (bez punktu, `RPS_WARMUP`), napisy rundy „PUNKT DLA…” odróżnione od wyniku meczu.
- Kilka otwartych stron naraz: przyczyna „skaczącej” gry w podglądzie. Każda strona sama wybierała dłonią
  karty i przyciski, a przy różnym lustrze w różnych miejscach. Teraz lustro ustawia serwer (`/api/mirror`),
  wybór niesie numer ekranu (`mode.scene`), a `/api/clients` pokazuje otwarte strony i ich wersję.
- Przełącznik widoków w stopce z lewej na każdym ekranie; wybór trzyma się przez grę / jazdę.
- Rytm odliczania 0,8 s (`beat_s` w stanie gry, z niego korzysta dłoń robota i symulacja). Powtórka: dwa paski
  Robot / Człowiek od pokazania gestu. Napis "Jadę …%" i panel radaru liczą prędkość z tej samej komendy kół.
- Brak gestu: przypomnienie z trzema gestami, za drugim razem runda dla robota (walkower, `forfeit_after`).
- Arena: napis spoza bieżącej fazy dostaje `display: none` (atrybut `hidden`, `tidy` w `game.js`), więc żadna
  zawieszona animacja go nie pokaże. Błędy areny trafiają do `window.__arenaErrors`. Zrzuty z nakładającymi się
  napisami z 2026-11-22 nie dały się odtworzyć w Chrome bez okna; do sprawdzenia w przeglądarce, w której wystąpiły.
- Radar: pełne koło, w nagłówku sam tytuł, podpisy obiektów bez nakładania.
- Bez widoku "Podczerwień" (tylko kolor i głębia), w grze koła stoją (`RPS_WHEELS=0`). Kiosk na Raspberry
  rysuje 20 klatek/s i nie czyści pustych płócien (przeglądarka brała ~90% procesora i grzała Pi do 74°C).
- Wentylator Pi 5 kręci się (sprawdzone); odczyt 0 obr./min = wiatrak bez przewodu obrotów.
- Gra do 3 wygranych (`RPS_WINS=3`, limit `RPS_ROUNDS=9`), oszukiwanie tylko w rundzie decydującej 2:2
  (pigułka „RUNDA DECYDUJĄCA”, powtórka tylko wtedy).
- Wybór dłonią (`Dwell` w `frontend/menu.js`): kursor wygładzony, cel z histerezą, 0,5 s „łaski” przy zgubieniu
  dłoni, 1,6 s przytrzymania (karty, przyciski po meczu), „‹ MENU” 2 s i nieaktywne w trakcie rundy gry.
- Bez kiosku: Raspberry 48,8°C tuż po wgraniu (wcześniej 71–78°C z kioskiem), 7–8% procesora w spoczynku.
- Docelowo: robot = sam serwer, ekran z laptopa (`KIOSK=0` w `.env` robota; `scripts/kiosk.sh` wtedy kończy
  od razu). Do Raspberry nie jest podpięty monitor, a kiosk rysował w tle ~1/4 procesora.
- Procesor 1,8 GHz (`PI_ARM_FREQ`) nie obniżył temperatury Raspberry (76–78°C), wrócono do 2,4 GHz.
  Raspberry ma radiator i wiatrak, a przy ~1/3 obciążenia trzyma 71–78°C: do sprawdzenia przepływ
  powietrza / docisk radiatora pod płytką RaspRovera.
- Kamera: `OAK_INTERNAL_FPS=22` (było 29).
- Kamera chłodniej: projektor IR tylko przy widoku głębi (`OAK_IR_ALWAYS=0`), wykrywanie osób wstrzymane w grze
  (sterowanie `person_ctrl` do skryptu na VPU). Szacunek −3…−6°C na procesorze kamery; do zmierzenia na łaziku. Czujnik i stereo liczyły klatki, których śledzenie dłoni nie zużywało.
- Steruje dłoń bliższa kamery: skrypt na VPU z dwóch dłoni z detektora bierze większą (vendor/…/VENDORED.md, zmiana 2),
  potem śledzi ją, dopóki jej nie zgubi. Bez trybu dwóch dłoni (brak pliku duo, mniej klatek). Do sprawdzenia na łaziku.
- Do decyzji: pełny model punktów dłoni (`hand_landmark_full_sh4.blob`, 11,9 MB, repozytorium geaxgx).
- Gesty: dopasowanie do wzorca (`decide`), głosowanie klatek od "Teraz!" i wybór najczęstszego gestu na końcu okna
  (okno 1,8 s). Zgłoszenie: na łaziku gest często nie wchodził, gdy dłoń nie była mocno wysunięta. Do dostrojenia
  progów na danych z `/api/game/debug`. Podziałka radaru na osi w prawo, bez "pigułek".
- 2026-11-22 na łaziku: radar był obrócony o 180° (kalibracja sprzed przełożenia kamery na stronę włącznika).
  Na robocie `LIDAR_ANGLE_OFFSET_DEG=90` (było -90); sprawdzone: osoba z LiDAR-u pokrywa się z dłonią z kamery,
  ściana za robotem jest u góry radaru. Kamera łączy się raz po USB 3, raz po USB 2 (styk / zasilanie).
- Podgląd jazdy w karcie: krótsze podpisy („Bliżej = szybciej”, „Pięść = do tyłu”, „Dalej = wolniej”),
  zawsze po lewej stronie skosu. Podpowiedź w menu na środku pola między kartami a stopką.

## 2026-11-21: nowe funkcje (sprawdzone w symulacji i testach, NIE na łaziku)

- Gra papier, kamień, nożyce (kciuk w górę), ostatnia runda „oszukana” z powtórką w zwolnionym tempie,
  wynik na OLED łazika (`T:3` / `T:-3`).
- Widoki „Głębia” i „Podczerwień” (bramka-skrypt na VPU + `/vision.mjpg`), sceny, gdy nikt nie steruje.
- Ekran startowy: wybór gry dłonią (kursor = dłoń z kamery, przytrzymanie ~1,3 s) albo dotykiem;
  jazda dopiero po wybraniu „Jedź robotem”, powrót do menu po `DRIVE_IDLE_S` bez dłoni.
- Arena gry: hologram dłoni robota z animacją (pięść palec po palcu, pompowanie w rytm, rzut, taniec / opadnięcie),
  bez dźwięku i efektów na tle (prośba użytkownika: minimalistycznie, na stoisku nie ma dźwięku).
- Do sprawdzenia na sprzęcie:
  1. czy potok hand trackera z bramką głębi startuje na OAK-D Pro (w razie odmowy VPU kamera startuje
     bez niej – w logu „obraz głębi wyłączony”),
  2. płynność obrazu kolorowego, gdy widać głębię (USB 2 vs niebieski USB 3; ewentualnie `OAK_VISION_EVERY_N=4`),
  3. czy klasyfikator gestów (4 palce, bez kciuka) i kciuk w górę działają na prawdziwych dłoniach
     (różne odległości, lewa ręka, rękaw),
  4. czy firmware RaspRovera reaguje na `{"T":3,...}` / `{"T":-3}` (ekranik OLED).
- Zmiany sprzętowe zgłoszone przez użytkownika (niezweryfikowane): wymieniony silnik koła, które stawało;
  włożone naładowane 18650; **kamera patrzy teraz w stronę włącznika** – sprawdzić, czy `DRIVE_FLIP`
  nadal pasuje (otwarta dłoń = koła kręcą się „w stronę kamery”) i ponownie skalibrować radar;
  OAK-D Pro na jednym kablu z Pi (Luxonis zaleca kabel Y przy projektorze IR – obserwować rozłączenia).

## Co działa (sprawdzone na łaziku)

- Raspberry Pi 5 + Docker: autostart po podłączeniu akumulatorów (kontener `restart: unless-stopped`,
  kiosk Chromium po autologowaniu do pulpitu).
- OAK-D Pro (kolor + MediaPipe Hands na VPU, ok. 13 kl./s – kamera jest na porcie USB 2.0;
  niebieski port USB 3 da płynniejszy obraz).
- LiDAR D500 na `/dev/ttyUSB0`, 10 obr./s; tarcza hamowania uczy się tła po starcie.
- ESP32 przez UART na GPIO `/dev/ttyAMA0` (Pi wpięte w 40 pinów RaspRovera, bez kabla USB do ESP32).
- Typ podwozia RaspRover (`{"T":900,"main":1,"module":0}`) wysyłany po każdym połączeniu –
  bez tego koła kręciły się w złą stronę.
- Jazda: otwarta dłoń = prosto do przodu, pięść = prosto do tyłu, bez skrętów.
- Kalibracja radaru w aplikacji: 5 kliknięć w logo Koła + hasło `KNML` (wynik w `data/calibration.json`).

## Ustawienia na łaziku (`~/knml-robot/.env`, różnice względem `.env.example`)

| Zmienna | Wartość | Dlaczego |
|---|---|---|
| `ESP32_PORT` | `/dev/ttyAMA0` | komendy ruchu działają tylko przez UART na GPIO |
| `DRIVE_FLIP` | `1` | kamera patrzy w stronę gniazda ładowania = tył podwozia |
| `ATTRACT` | `0` | samoczynne kręcenie kołami wyglądało jak usterka |
| `LIDAR_ANGLE_OFFSET_DEG` | `-90` | strzałka (zero) LiDAR-u wskazuje w prawo; dokładnie ustawi kalibracja |

Wgrywanie: `./scripts/deploy_rpi.sh` (zachowuje `.env` i `data/` na łaziku).

## Do naprawy

### 1. Przednie prawe koło rusza i staje

Patrząc jak kamera: przednie prawe = dla płytki tylne koło LEWEJ strony (kamera jest z tyłu podwozia).
Kręci się na starcie, potem staje; pozostałe trzy koła działają.

Płytka ma po jednym enkoderze na stronę (`L`/`R`, `odl`/`odr` w `T:1001`), więc oba silniki strony
dostają tę samą moc. Hipotezy:

- **Regulator prędkości (`T:1`) zbija moc** – w powietrzu kołu z enkoderem wystarcza mało mocy,
  słabszy / bardziej oporny silnik przy tej mocy staje. Wtedy w `T:11` (bezpośrednie PWM)
  koło powinno kręcić się cały czas → poprawka w `backend/app/hw/esp32.py`: sterowanie przez
  `T:11` z minimalnym PWM albo minimalna prędkość w `T:1`.
- **Sprzęt** – luźna wtyczka silnika na płytce, uszkodzony przewód, zacinająca się przekładnia
  (sprawdzić obracając koło ręką; zamienić wtyczki silników tej samej strony).

Test: `scripts/test_trybow_napedu.py` (koła w powietrzu, instrukcja w pliku).

### 2. Raspberry wyłączyło się podczas testu kół (czerwona dioda)

Podczas pierwszej wersji testu trybów (z krokiem PWM 180 na wszystkich kołach) Pi przeszło w stan
wyłączenia (czerwona dioda), pomogło ponowne włączenie. Po restarcie: zasilanie 5,33 V, brak
spowolnień (`get_throttled=0x0`), `power_reset=0`. Logów sprzed wyłączenia nie było, bo dziennik
systemowy nie był trwały. Podejrzenie: spadek napięcia przy dużym poborze prądu silników.

- Sprawdzić, czy dziennik jest już trwały: `journalctl --list-boots` (powinno być kilka startów).
  Jeśli nie: `sudo mkdir -p /var/log/journal && sudo systemctl restart systemd-journald`
  (warto dopisać do `scripts/setup_rpi.sh`). Po kolejnym wyłączeniu: `sudo journalctl -b -1 -k`.
- Normalna jazda dłonią (do 0,5 m/s) nie wyłączała Pi.

### 3. Pozostałe

- Kalibracja radaru do zrobienia na stoisku, gdy robot stoi już na swoim miejscu.
- Temperatura bez wentylatora: ok. 75–80°C, Pi samo zwalnia przy 80–85°C. Wentylator Pi 4 nie pasuje
  (2 piny; złącze wentylatora Pi 5 jest zasłonięte przez płytkę RaspRovera).
- Wi-Fi: profil `netplan-wlan0-…` (hotspot telefonu) z automatycznym łączeniem; nieprzetestowany
  na żywo.
