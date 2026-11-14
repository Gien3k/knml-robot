"""Atrapy urządzeń szeregowych na pseudoterminalach (pty) – testy bez sprzętu.

Każda atrapa ma ścieżkę (/dev/pts/N), którą kod otwiera dokładnie tak jak prawdziwy port,
i zapisuje WSZYSTKO, co host do niej wysłał (self.received).
"""
from __future__ import annotations

import json
import math
import os
import select
import threading
import time

from app.protocol.ld19 import build_packet

if os.name == "posix":
    import pty
    import tty


class FakePort:
    def __init__(self) -> None:
        self.master, self._slave = pty.openpty()
        tty.setraw(self._slave)                  # bez echa: nic nie "wraca" do atrapy
        os.set_blocking(self.master, False)
        self.path = os.ttyname(self._slave)
        self.received = bytearray()
        self._halt = threading.Event()
        self._lock = threading.Lock()
        self._threads = [threading.Thread(target=self._reader, daemon=True)]

    def start(self) -> "FakePort":
        for t in self._threads:
            t.start()
        return self

    def send(self, data: bytes) -> None:
        try:
            os.write(self.master, data)
        except (BlockingIOError, OSError):
            pass                                  # nikt nie czyta – jak prawdziwe urządzenie

    def _reader(self) -> None:
        while not self._halt.is_set():
            r, _, _ = select.select([self.master], [], [], 0.02)
            if not r:
                continue
            try:
                data = os.read(self.master, 4096)
            except (BlockingIOError, OSError):
                continue
            if data:
                with self._lock:
                    self.received += data
                self.on_data(data)

    def on_data(self, data: bytes) -> None:
        pass

    def close(self) -> None:
        self._halt.set()
        for t in self._threads:
            t.join(timeout=1.0)
        for fd in (self.master, self._slave):
            try:
                os.close(fd)
            except OSError:
                pass


class FakeLidar(FakePort):
    """Nadaje ramki LD19 jak prawdziwy D500 (~10 obr./s). Przeszkoda: stała ściana 2 m."""

    def __init__(self, obstacle_m: float = 2.0) -> None:
        super().__init__()
        self.obstacle_mm = obstacle_m * 1000
        self._threads.append(threading.Thread(target=self._writer, daemon=True))

    def _writer(self) -> None:
        start = 0
        ts = 0
        while not self._halt.is_set():
            chunk = b""
            for _ in range(8):                    # ~8 pakietów / 20 ms ≈ 400 pakietów/s
                end = start + 1100
                chunk += build_packet(start, end, [self.obstacle_mm] * 12, speed=3600, timestamp=ts)
                start = (end + 100) % 36000
                ts += 2
            self.send(chunk)
            time.sleep(0.02)


class FakeEsp32(FakePort):
    """Odpowiada na {"T":130}; w trybie "chatty" sam nadaje T:1001 co 100 ms."""

    def __init__(self, chatty: bool = True, boot_garbage: bool = False) -> None:
        super().__init__()
        self.chatty = chatty
        self.boot_garbage = boot_garbage
        self.lines: list[dict] = []
        self._buf = b""
        self._threads.append(threading.Thread(target=self._writer, daemon=True))

    def _telemetry(self) -> bytes:
        return (json.dumps({"T": 1001, "L": 0, "R": 0, "r": 0.1, "p": -0.2, "y": 12.0, "v": 12.05}) + "\n").encode()

    def _writer(self) -> None:
        if self.boot_garbage:
            self.send(b"ets Jun  8 2016 00:22:57\r\nrst:0x1 (POWERON_RESET),boot:0x13\r\n")
        while not self._halt.is_set():
            if self.chatty:
                self.send(self._telemetry())
            time.sleep(0.1)

    def on_data(self, data: bytes) -> None:
        self._buf += data
        *lines, self._buf = self._buf.split(b"\n")
        for ln in lines:
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            self.lines.append(obj)
            if obj.get("T") == 130:
                self.send(self._telemetry())

    def motion(self) -> list[dict]:
        return [o for o in list(self.lines) if o.get("T") == 1]


class FakeNoise(FakePort):
    """Urządzenie nadające binarny szum (np. coś innego niż nasz sprzęt)."""

    def __init__(self) -> None:
        super().__init__()
        self._threads.append(threading.Thread(target=self._writer, daemon=True))

    def _writer(self) -> None:
        k = 0
        while not self._halt.is_set():
            self.send(bytes((int(127 + 120 * math.sin(k + i)) & 0xFF) | 0x80 for i in range(64)))
            k += 1
            time.sleep(0.02)
