"""ESP32 (Waveshare RaspRover) – wątek wysyłający prędkości kół 20 Hz.

* Po otwarciu portu: stop, typ podwozia RaspRover (T:900 – firmware go nie pamięta),
  komendy z ESP32_INIT.
* DRIVE_FLIP=1 (kamera z tyłu podwozia): (L,R) -> (-R,-L) przy wysyłaniu i odwrotnie przy
  odczycie, więc reszta programu zawsze liczy "przód" = kierunek kamery.
* Wysyła ZAWSZE, także zera (heartbeat firmware'u nie zatrzyma kół w trakcie jazdy).
* Prędkość bierze z hub.drive_cmd; komenda starsza niż PERCEPTION_TIMEOUT_S (pętla
  sterowania padła), aktywna tarcza LiDAR albo brak zgody => wysyłamy zero.
* STOP awaryjny: hub.estop_event budzi wątek natychmiast i wysyła zero 3 razy.
* Brak odpowiedzi przez 1 s => zapytanie {"T":130}; przez 2 s => zwolnienie portu.
* Wyjście z wątku (błąd, zatrzymanie) => zawsze próba wysłania stopu.
* Ekranik OLED: hub.oled (4 linie, np. wynik gry) – wysyłane tylko zmienione linie, najwyżej
  co OLED_MIN_S; None = ekran domyślny firmware'u (przywracany też po każdym połączeniu).
"""
from __future__ import annotations

import logging
import threading
import time

from ..config import Settings
from ..hub import Hub
from ..protocol import waveshare
from .serial_util import open_port

log = logging.getLogger("esp32")

PERIOD_S = 0.05
OLED_MIN_S = 0.25
_UNSET = object()
QUERY_AFTER_S = 1.0
RELEASE_AFTER_S = 2.0


def flip(left: float, right: float) -> tuple[float, float]:
    """Obrót o 180°: przód <-> tył i lewa <-> prawa. Działa w obie strony (flip(flip(x)) == x)."""
    return -right, -left


class Esp32Driver(threading.Thread):
    role = "esp32"

    def __init__(self, hub: Hub, port: str, settings: Settings, opener=open_port) -> None:
        super().__init__(daemon=True, name="esp32")
        self.hub, self.port, self.s = hub, port, settings
        self.opener = opener
        self._halt = threading.Event()
        self.error: str | None = None
        self.sent = 0
        self.last_rx = 0.0
        self._oled = _UNSET
        self._oled_t = 0.0

    def stop(self) -> None:
        self._halt.set()
        self.hub.estop_event.set()          # obudź pętlę, żeby od razu wysłała stop

    def run(self) -> None:
        try:
            self._loop()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.warning("ESP32 %s: %s", self.port, self.error)

    def _send_stop(self, ser, times: int = 3) -> None:
        for i in range(times):
            ser.write(waveshare.STOP)
            if i < times - 1:
                time.sleep(0.01)

    def _update_oled(self, ser, now: float) -> None:
        if not self.s.oled:
            return
        want = self.hub.oled
        want = None if want is None else [str(x) for x in want[:4]]
        if want == self._oled or now - self._oled_t < OLED_MIN_S:
            return
        self._oled_t = now
        if want is None:
            ser.write(waveshare.OLED_DEFAULT)
        else:
            prev = self._oled if isinstance(self._oled, list) else [None] * 4
            for i, line in enumerate(want):
                if i >= len(prev) or prev[i] != line:
                    ser.write(waveshare.oled(i, line))
        self._oled = want

    def _loop(self) -> None:
        hub, s = self.hub, self.s
        ser = self.opener(self.port, waveshare.BAUD, 0)
        try:
            self._send_stop(ser, 1)
            if s.esp32_chassis:
                # bez typu podwozia firmware źle kręci kołami (kierunek, prędkość)
                ser.write(waveshare.chassis(s.esp32_chassis, s.esp32_module))
                time.sleep(0.05)
            for cmd in waveshare.parse_init(s.esp32_init):
                ser.write(waveshare.encode(cmd))
            # (estop_event celowo NIE jest czyszczony: sygnał sprzed startu = 3 dodatkowe stopy, nic złego)
            buf = b""
            now = time.monotonic()
            self.last_rx = now
            last_query = 0.0
            next_tick = now
            while not self._halt.is_set():
                if hub.estop_event.wait(max(0.0, next_tick - time.monotonic())):
                    hub.estop_event.clear()
                    self._send_stop(ser, 3)
                    if self._halt.is_set():
                        break
                    if hub.shield_state.active:
                        hub.on_stop_sent(time.monotonic())      # pomiar czasu reakcji hamulca
                now = time.monotonic()

                # --- odbiór telemetrii ---
                n = ser.in_waiting
                if n:
                    buf += ser.read(n)
                    *lines, buf = buf.split(b"\n")
                    buf = buf[-1024:]
                    for ln in lines:
                        obj = waveshare.parse_line(ln)
                        if obj is None:
                            continue
                        self.last_rx = now
                        if obj.get("T") == waveshare.FB_BASE:
                            tel = {"t": now, "voltage": waveshare.voltage(obj)}
                            if isinstance(obj.get("L"), (int, float)) and isinstance(obj.get("R"), (int, float)):
                                tel["L"], tel["R"] = float(obj["L"]), float(obj["R"])
                                if s.drive_flip:
                                    tel["L"], tel["R"] = flip(tel["L"], tel["R"])
                            hub.esp32 = tel

                # --- 20 Hz: komenda prędkości (zawsze) ---
                if now >= next_tick:
                    cmd = hub.drive_cmd
                    ok = (cmd.get("allowed") and now - cmd.get("t", 0.0) <= s.perception_timeout_s
                          and not hub.shield_state.active)
                    left, right = (cmd["L"], cmd["R"]) if ok else (0.0, 0.0)
                    ml, mr = flip(left, right) if s.drive_flip else (left, right)
                    ser.write(waveshare.speed(ml, mr, s.max_speed))
                    self.sent += 1
                    tel = hub.esp32
                    if tel.get("t") and now - tel["t"] < 0.5 and "L" in tel:
                        hub.on_wheels(tel["L"], tel["R"], "esp32", now)
                    else:
                        hub.on_wheels(left, right, "cmd", now)
                    next_tick += PERIOD_S
                    if next_tick < now - 0.25:          # zaległości (np. przycięcie systemu)
                        next_tick = now + PERIOD_S
                    self._update_oled(ser, now)

                # --- czy ESP32 żyje ---
                silent = now - self.last_rx
                if silent > QUERY_AFTER_S and now - last_query > 0.5:
                    ser.write(waveshare.QUERY)
                    last_query = now
                if silent > RELEASE_AFTER_S:
                    raise IOError(f"ESP32 nie odpowiada od {RELEASE_AFTER_S:.0f} s")
        finally:
            try:
                self._send_stop(ser, 3)
                if self._oled not in (_UNSET, None):
                    ser.write(waveshare.OLED_DEFAULT)
            except Exception:
                pass
            try:
                ser.close()
            except Exception:
                pass
