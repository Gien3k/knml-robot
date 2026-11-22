"""Protokół JSON płytki ESP32 Waveshare (RaspRover / UGV, firmware "General Driver for Robots").

WSZYSTKIE stałe protokołu są tutaj. Źródła (sprawdzone 2026-11):
  * https://www.waveshare.com/wiki/08_Sub-controller_JSON_Command_Set
  * https://www.waveshare.com/wiki/Jetson_02_Python_Chassis_Motion_Control
  * https://github.com/waveshareteam/ugv_rpi (base_ctrl.py: '/dev/ttyAMA0', 115200)

Ramki: jedna linia JSON zakończona '\\n', UART 115200 8N1.

  {"T":1,"L":0.2,"R":0.2}   prędkość lewej / prawej strony [m/s], ujemna = do tyłu,
                            0 = stop. Zakres zależy od silników; dla RaspRover
                            przyjmujemy ±0,5 m/s (MAX_SPEED).
  {"T":130}                 jednorazowe zapytanie o stan podwozia (odpowiedź T:1001)
  {"T":131,"cmd":1}         ciągła telemetria włączona (domyślnie WŁĄCZONA po starcie)
  {"T":142,"cmd":<ms>}      odstęp ciągłej telemetrii
  {"T":143,"cmd":0}         echo komend wyłączone
  {"T":900,"main":1,"module":0}
                            TYP PODWOZIA – trzeba wysłać po KAŻDYM włączeniu (firmware go nie
                            pamięta). main: 1 = RaspRover (4 koła), 2 = UGV Rover (6 kół),
                            3 = UGV Beast (gąsienice); module: 0 = brak / bez odczytu modułu.
                            Bez tego koła kręcą się w złą stronę i z inną prędkością
                            (wiki Waveshare "Jetson 02 Python Chassis Motion Control").
  {"T":136,"cmd":<ms>}      heartbeat: brak komendy ruchu przez <ms> => firmware sam
                            zatrzymuje koła (dokumentacja: "wysyłaj komendy ruchu co 2-4 s",
                            przykład 3000 ms). Wysyłamy 20 Hz, więc zapas jest ogromny.
  {"T":3,"lineNum":0,"Text":"..."}
                            ekranik OLED 0,91" (4 linie, lineNum 0..3): własny tekst zastępuje
                            ekran startowy; za długi tekst zawija się na następną linię
  {"T":-3}                  przywrócenie domyślnego ekranu OLED
  odpowiedź: {"T":1001, "L":.., "R":.., "r":.., "p":.., "y":.., "v":.., ...}
            pola zależą od wersji firmware; "v" = napięcie zasilania.
"""
from __future__ import annotations

import json

BAUD = 115200

CMD_SPEED = 1
CMD_OLED = 3
CMD_OLED_DEFAULT = -3
CMD_FEEDBACK_QUERY = 130
CMD_FEEDBACK_FLOW = 131
CMD_HEARTBEAT = 136
CMD_FEEDBACK_INTERVAL = 142
CMD_ECHO = 143
CMD_CHASSIS = 900
FB_BASE = 1001

QUERY = b'{"T":130}\n'
STOP = b'{"T":1,"L":0,"R":0}\n'
OLED_DEFAULT = b'{"T":-3}\n'
OLED_COLS = 21                  # 128 px / 6 px na znak

# Komendy, które NIE poruszają kołami (bezpieczne do wysłania przy konfiguracji)
NON_MOTION = {CMD_FEEDBACK_QUERY, CMD_FEEDBACK_FLOW, CMD_HEARTBEAT, CMD_FEEDBACK_INTERVAL, CMD_ECHO, CMD_CHASSIS,
              CMD_OLED, CMD_OLED_DEFAULT}

RASPROVER = 1


def chassis(main: int, module: int = 0) -> bytes:
    """Ustawienie typu podwozia (T:900) – pierwsza komenda po otwarciu portu."""
    return encode({"T": CMD_CHASSIS, "main": int(main), "module": int(module)})


_PL = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def oled(line: int, text: str) -> bytes:
    """Jedna linia ekraniku OLED (tylko ASCII – polskie znaki bez ogonków, przycięte do szerokości)."""
    t = str(text).translate(_PL).encode("ascii", "replace").decode()[:OLED_COLS]
    return encode({"T": CMD_OLED, "lineNum": max(0, min(3, int(line))), "Text": t})


def encode(obj: dict) -> bytes:
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode()


def speed(left: float, right: float, limit: float) -> bytes:
    lim = abs(limit)
    left = max(-lim, min(lim, float(left)))
    right = max(-lim, min(lim, float(right)))
    return encode({"T": CMD_SPEED, "L": round(left, 3), "R": round(right, 3)})


def parse_line(line: bytes) -> dict | None:
    """Linia z ESP32 -> dict z kluczem "T" albo None (śmieci po starcie, logi bootloadera)."""
    line = line.strip()
    if not line.startswith(b"{") or not line.endswith(b"}"):
        return None
    try:
        obj = json.loads(line)
    except ValueError:
        return None
    if isinstance(obj, dict) and "T" in obj:
        return obj
    return None


def parse_init(spec: str) -> list[dict]:
    """ESP32_INIT: komendy konfiguracyjne rozdzielone ';'. Komendy ruchu są odrzucane."""
    out = []
    for part in spec.split(";"):
        part = part.strip()
        if not part:
            continue
        try:
            obj = json.loads(part)
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("T") in NON_MOTION:
            out.append(obj)
    return out


def voltage(fb: dict) -> float | None:
    """Napięcie akumulatora [V] z pola "v".

    Firmware RaspRover podaje SETNE części wolta jako liczbę całkowitą
    (sprawdzone na sprzęcie: "v":59 przy ekraniku "V 0.59"; 1206 = 12,06 V).
    Wartość z kropką dziesiętną traktujemy jako wolty (starsze wersje firmware).
    """
    v = fb.get("v")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, int) or v > 100:
        v = v / 100.0
    return round(float(v), 2)


NO_BATTERY_V = 5.0              # poniżej = w koszyku nie ma ogniw (łazik na samej ładowarce)
