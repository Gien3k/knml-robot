"""Otwieranie portów szeregowych tak, żeby NIE zresetować ESP32.

Płytki ESP32 z mostkiem USB-UART mają układ auto-resetu sterowany liniami DTR/RTS
(EN = reset, IO0 = tryb programowania). Reset następuje, gdy DTR jest zwolnione,
a RTS ustawione (dtr=False, rts=True). Linux przy open() ustawia OBIE linie,
a pyserial z dtr=False, rts=False ustawionymi przed open() najpierw zwalnia DTR,
a dopiero potem RTS – przez chwilę trwa stan resetujący.

Dlatego ustawiamy przed open() tylko rts=False (pyserial: DTR=1, potem RTS=0;
stan dtr=1/rts=0 nie resetuje), a DTR zwalniamy zaraz po otwarciu. Kolejność
stanów: (1,1) -> (1,0) -> (0,0), nigdy (0,1).
UART na GPIO (/dev/ttyAMA0) i pty nie mają linii modemowych – błędy ioctl ignorujemy.
"""
from __future__ import annotations

import os

import serial


class PortBusy(Exception):
    pass


def open_port(path: str, baud: int, timeout: float = 0.05):
    ser = serial.Serial()
    ser.port = path
    ser.baudrate = baud
    ser.timeout = timeout
    ser.write_timeout = 0.5
    ser.rts = False                 # patrz docstring – DTR celowo zostaje domyślne
    if os.name == "posix":
        ser.exclusive = True        # drugi proces nie otworzy portu (flock)
    try:
        ser.open()
    except serial.SerialException as e:
        msg = str(e).lower()
        if "exclusive" in msg or "lock" in msg or "busy" in msg or "resource temporarily" in msg:
            raise PortBusy(str(e)) from e
        raise
    try:
        ser.dtr = False
    except (OSError, serial.SerialException):
        pass
    return ser


class ReadOnlyPort:
    """Opakowanie portu LiDAR-u: zapis jest fizycznie niemożliwy z poziomu kodu."""

    def __init__(self, ser) -> None:
        self._ser = ser

    def read(self, n: int = 1) -> bytes:
        return self._ser.read(n)

    def reset_input_buffer(self) -> None:
        self._ser.reset_input_buffer()

    @property
    def in_waiting(self) -> int:
        return self._ser.in_waiting

    def write(self, data: bytes) -> int:   # noqa: ARG002
        raise PermissionError("Zapis do portu LiDAR-u jest zabroniony")

    def close(self) -> None:
        self._ser.close()
