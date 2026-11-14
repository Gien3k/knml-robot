# Stan projektu i znane problemy (2026-11-14)

Notatka do dalszej pracy. Instrukcja uruchomienia i opis architektury: [README.md](README.md).

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
