"""Test kół łazika – krótko i wolno, z odczytem prędkości zmierzonej przez ESP32.

ROBOT MUSI STAĆ NA PODSTAWCE (koła w powietrzu)!

Uruchamianie z laptopa (serwer robota jest na chwilę zatrzymywany, żeby zwolnić port):
  ssh -i ~/.ssh/knml_lazik knml@lazik.local \
    'cd ~/knml-robot && docker compose stop robot && docker compose run --rm -T robot python - ; docker compose start robot' \
    < scripts/test_kol.py

Najpierw typ podwozia RaspRover (T:900), potem kolejno: lewa strona do przodu, prawa do przodu,
wszystkie do przodu, wszystkie do tyłu – po 3 s przy 0,15 m/s, 2 s przerwy, na końcu zawsze STOP.
Wypisuje zadaną i zmierzoną prędkość (ze znakiem) i najniższe napięcie.
"""
import glob
import json
import sys
import time

import serial

SPEED = 0.15        # m/s – wolno, koła w powietrzu
STEP_S = 3.0        # dość długo, żeby zobaczyć, które koło się kręci
PAUSE_S = 2.0
# port można wymusić argumentem: ... python - /dev/ttyACM0
# RaspRover: komendy ruchu działają tylko przez UART na GPIO, więc on idzie pierwszy
PORTS = sys.argv[1:] or (["/dev/ttyAMA0", "/dev/ttyACM0"] + sorted(glob.glob("/dev/ttyUSB*")))
WAIT_S = 5.0 if sys.argv[1:] else 1.5   # ESP32 może się zrestartować przy otwarciu portu USB


def open_port(path: str) -> serial.Serial:
    """Jak serwer (app/hw/serial_util.py): RTS zwolnione przed open(), DTR dopiero po –
    kolejność stanów (1,1) -> (1,0) -> (0,0) nie resetuje ESP32 przez mostek USB."""
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = path, 115200, 0.02
    s.rts = False
    s.exclusive = True
    s.open()
    try:
        s.dtr = False
    except (OSError, serial.SerialException):
        pass                 # UART na GPIO nie ma linii modemowych
    return s


def send(ser: serial.Serial, obj: dict) -> None:
    ser.write((json.dumps(obj, separators=(",", ":")) + "\n").encode())


buf = b""


def feedback(ser: serial.Serial) -> list[dict]:
    global buf
    buf += ser.read(4096)
    *lines, buf = buf.split(b"\n")
    out = []
    for ln in lines:
        try:
            o = json.loads(ln.strip())
        except ValueError:
            continue
        if isinstance(o, dict) and o.get("T") == 1001:
            out.append(o)
    return out


def find_esp32() -> serial.Serial:
    for p in PORTS:
        try:
            ser = open_port(p)
        except Exception:
            continue
        send(ser, {"T": 131, "cmd": 1})
        t_end = time.time() + WAIT_S
        while time.time() < t_end:
            send(ser, {"T": 130})
            if feedback(ser):
                print(f"ESP32 na {p}")
                return ser
            time.sleep(0.15)
        ser.close()
    raise SystemExit("Nie znaleziono ESP32 (brak odpowiedzi T:1001 na żadnym porcie)")


def volts(fb: dict) -> float | None:
    v = fb.get("v")
    if not isinstance(v, (int, float)):
        return None
    return v / 100.0 if v > 100 else float(v)


ser = find_esp32()
# typ podwozia RaspRover – bez tego firmware kręci kołami w złą stronę (wiki Waveshare)
CHASSIS = int(__import__("os").environ.get("ESP32_CHASSIS", "1"))
if CHASSIS:
    send(ser, {"T": 900, "main": CHASSIS, "module": 0})
    print(f"typ podwozia ustawiony: main={CHASSIS}")
time.sleep(0.5)
feedback(ser)
steps = [("1/4  LEWA strona do przodu", SPEED, 0.0), ("2/4  PRAWA strona do przodu", 0.0, SPEED),
         ("3/4  wszystkie koła do przodu", SPEED, SPEED), ("4/4  wszystkie koła do tyłu", -SPEED, -SPEED)]
try:
    for name, left, right in steps:
        sl = sr = 0.0
        n = 0
        v_min = None
        t_end = time.time() + STEP_S
        while time.time() < t_end:
            send(ser, {"T": 1, "L": left, "R": right})
            for fb in feedback(ser):
                sl += float(fb.get("L", 0) or 0)
                sr += float(fb.get("R", 0) or 0)
                n += 1
                v = volts(fb)
                if v is not None:
                    v_min = v if v_min is None else min(v_min, v)
            time.sleep(0.1)
        send(ser, {"T": 1, "L": 0, "R": 0})
        time.sleep(PAUSE_S)
        feedback(ser)
        vt = f"{v_min:.2f} V" if v_min is not None else "brak odczytu"
        ml, mr = (sl / n, sr / n) if n else (0.0, 0.0)
        print(f"{name:32s} zadane L={left:+.2f} R={right:+.2f}  zmierzone L={ml:+.3f} R={mr:+.3f} m/s  "
              f"napięcie min {vt}", flush=True)
finally:
    for _ in range(5):
        send(ser, {"T": 1, "L": 0, "R": 0})
        time.sleep(0.05)
    ser.close()
    print("STOP wysłany – koła zatrzymane")
