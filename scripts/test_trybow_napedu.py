"""Diagnostyka napędu: które tryby sterowania kręcą WSZYSTKIMI kołami (koła w powietrzu!).

Po co: przednie prawe koło (patrząc jak kamera) rusza na starcie i staje. Płytka ma po jednym
enkoderze na stronę (pola L/R, odl/odr w T:1001), więc regulator prędkości (T:1) może zbić moc
całej strony tak nisko, że słabszy silnik staje. T:11 (bezpośrednie PWM) to omija.

Kroki po 4 s (przód = kierunek kamery = UJEMNE wartości dla płytki, bo kamera jest z tyłu podwozia):
  1. T:1  0.15 m/s   regulacja prędkości, wolno
  2. T:1  0.40 m/s   regulacja prędkości, szybko
  3. T:11 PWM 110    bezpośrednia moc (bez regulatora)
Obserwuj, w których krokach przednie prawe koło kręci się cały czas.

UWAGA: poprzednia wersja miała też krok PWM 180 na wszystkich kołach – podczas tego testu
Raspberry się wyłączyło (czerwona dioda). Dlatego go nie ma, a test przerywa się, gdy napięcie
akumulatora spadnie poniżej MIN_V.

Uruchamianie z laptopa (serwer robota jest na chwilę zatrzymywany, żeby zwolnić port):
  ssh -i ~/.ssh/knml_lazik knml@lazik.local \\
    'cd ~/knml-robot && docker compose stop robot && docker compose run --rm -T robot python - ; docker compose start robot' \\
    < scripts/test_trybow_napedu.py
"""
import json
import time

import serial

PORT = "/dev/ttyAMA0"
MIN_V = 10.8                    # 3S: poniżej tego przerywamy (spadek napięcia pod obciążeniem)
STEP_S, PAUSE_S = 4.0, 2.0
STAGES = [("1 wolno, regulacja prędkości (T:1 0.15 m/s)", {"T": 1, "L": -0.15, "R": -0.15}),
          ("2 szybko, regulacja prędkości (T:1 0.40 m/s)", {"T": 1, "L": -0.40, "R": -0.40}),
          ("3 bezpośrednia moc (T:11 PWM 110)", {"T": 11, "L": -110, "R": -110})]
STOP = {"T": 1, "L": 0, "R": 0}

ser = serial.Serial(PORT, 115200, timeout=0.02)
buf = b""


def send(o: dict) -> None:
    ser.write((json.dumps(o, separators=(",", ":")) + "\n").encode())


def feedback() -> list[dict]:
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


def volts(o: dict) -> float | None:
    v = o.get("v")
    if not isinstance(v, (int, float)):
        return None
    return v / 100.0 if v > 100 else float(v)


send(STOP)
send({"T": 900, "main": 1, "module": 0})       # typ podwozia RaspRover
send({"T": 131, "cmd": 1})                      # ciągła telemetria
time.sleep(0.5)
feedback()
t0 = time.time()
try:
    for name, cmd in STAGES:
        rows, v_min, abort = [], None, False
        t_end = time.time() + STEP_S
        while time.time() < t_end and not abort:
            send(cmd)
            for o in feedback():
                v = volts(o)
                if v is not None:
                    v_min = v if v_min is None else min(v_min, v)
                    abort = v < MIN_V
                rows.append((o.get("L", 0), o.get("R", 0), o.get("odl", 0), o.get("odr", 0)))
            time.sleep(0.1)
        send(STOP)
        if rows:
            half = rows[len(rows) // 2:]
            ml = sum(r[0] for r in half) / len(half)
            mr = sum(r[1] for r in half) / len(half)
            print(f"{name}\n   prędkość (2. połowa): lewa płytki {ml:+.3f}  prawa płytki {mr:+.3f} m/s   "
                  f"odometria dL={rows[-1][2] - rows[0][2]} dR={rows[-1][3] - rows[0][3]}   "
                  f"napięcie min {v_min if v_min is not None else '?'} V", flush=True)
        if abort:
            print(f"PRZERWANO: napięcie spadło do {v_min} V (< {MIN_V} V)")
            break
        t_end = time.time() + PAUSE_S
        while time.time() < t_end:
            send(STOP)
            feedback()
            time.sleep(0.1)
finally:
    for _ in range(5):
        send(STOP)
        time.sleep(0.05)
    ser.close()
    print("STOP wysłany – koła zatrzymane")
