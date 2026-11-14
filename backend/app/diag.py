"""Diagnostyka sprzętu: python -m app.diag   (w kontenerze: docker compose exec robot python -m app.diag)

Wypisuje każdy kandydujący port szeregowy, wynik sondowania (rola, ramki LD19,
JSON z ESP32, ile bajtów wysłano), wykryte kamery OAK oraz – jeśli serwer działa –
role, które serwer już przypisał. Port zajęty przez działający serwer jest
raportowany jako "busy" (to dobrze: serwer go używa).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

from .config import settings
from .hw.camera import depthai_version, list_oak_devices, usb_luxonis_present
from .hw.discovery import candidate_ports, probe

G, R, Y, D, B, X = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"
if not sys.stdout.isatty():
    G = R = Y = D = B = X = ""


def _server_state() -> dict | None:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/devices", timeout=1.5) as r:
            return json.loads(r.read())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnostyka sprzętu łazika")
    ap.add_argument("--no-query", action="store_true", help="nie wysyłaj zapytania {\"T\":130}")
    ap.add_argument("--all", action="store_true", help="sonduj wszystkie /dev/ttyUSB* i /dev/ttyACM*")
    args = ap.parse_args()

    print(f"{B}== Łazik 4WD · diagnostyka sprzętu =={X}")
    srv = _server_state()
    assigned = {}
    if srv:
        assigned = srv.get("supervisor", {}).get("assigned", {})
        print(f"{G}Serwer działa{X} – urządzenia według serwera:")
        for k, d in srv.get("devices", {}).items():
            col = G if d["state"] == "real" else (R if d["state"] == "error" else D)
            print(f"  {k:6s} {col}{d['state']:5s}{X} {d.get('detail', '')}  {D}{d.get('message', '')}{X}")
    else:
        print(f"{Y}Serwer nie odpowiada na :8000{X} – sonduję bez niego")

    print(f"\n{B}Porty szeregowe{X}")
    cands = candidate_ports(settings.serial_extra_ids, settings.serial_probe_all or args.all,
                            (settings.lidar_port, settings.esp32_port))
    if not cands:
        print(f"  {Y}brak kandydatów{X} – sprawdź kabel, `ls -l /dev/ttyUSB* /dev/ttyACM* /dev/ttyAMA0`,"
              " mapowanie /dev w docker-compose.rpi.yml")
    found = {"lidar": 0, "esp32": 0}
    for c in cands:
        vp = c.to_dict()["vidpid"] or "----:----"
        print(f"  {B}{c.device}{X} -> {c.realpath}  [{vp}] {c.description}  ({c.hint})")
        if c.realpath in assigned:
            role = assigned[c.realpath]
            found[role] = found.get(role, 0) + 1
            print(f"      {G}rola: {role}{X} (przypisana przez działający serwer)")
            continue
        res = probe(c.device, allow_query=not args.no_query)
        col = G if res.role in ("lidar", "esp32") else (Y if res.role == "busy" else R)
        if res.role in found:
            found[res.role] += 1
        print(f"      {col}rola: {res.role}{X}  ramki LD19={res.frames}  JSON={res.json_lines}  "
              f"bajty@230400={res.bytes_lidar}  bajty@115200={res.bytes_esp}  wysłano={res.wrote} B  "
              f"({res.duration_s:.1f} s)")
        print(f"      {D}{res.detail}{X}")

    print(f"\n{B}OAK-D (DepthAI){X}")
    print(f"  depthai: {depthai_version() or R + 'brak' + X}")
    usb = usb_luxonis_present()
    print(f"  USB 03e7 w sysfs: {('tak' if usb else 'nie') if usb is not None else 'sysfs niedostępny'}")
    try:
        devs = list_oak_devices()
    except Exception as e:
        devs = []
        print(f"  {R}getAllAvailableDevices: {e}{X}")
    for d in devs:
        print(f"  {G}{d['mxid'] or d['name']}{X} stan={d['state']}")
    if not devs:
        busy = srv and srv.get("devices", {}).get("oak", {}).get("state") == "real"
        print(f"  {G if busy else Y}{'kamera używana przez serwer' if busy else 'brak wolnych kamer'}{X}")
    if os.path.isdir("/dev/bus/usb"):
        print(f"  /dev/bus/usb: {'dostępne' if os.access('/dev/bus/usb', os.R_OK) else R + 'brak dostępu' + X}")

    print(f"\n{B}Podsumowanie{X}: LiDAR={found['lidar']}  ESP32={found['esp32']}  OAK={len(devs) or ('w użyciu' if srv and srv['devices']['oak']['state'] == 'real' else 0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
