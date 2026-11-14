"""Wykrywanie OAK-D nie może zamrażać serwera, gdy kontener nie ma dostępu do USB."""
import os

import pytest

from app.hw import camera


def test_no_usb_sysfs_skips_depthai(monkeypatch):
    if os.name != "posix":
        pytest.skip("dotyczy Linuksa (kontener)")
    monkeypatch.setattr(camera, "usb_luxonis_present", lambda: None)

    def boom():
        raise AssertionError("DepthAI nie powinno być wołane bez dostępu do USB")

    monkeypatch.setattr(camera, "list_oak_devices", boom)
    ok, msg = camera.oak_present()
    assert ok is False
    assert "USB" in msg


def test_no_luxonis_on_usb_skips_depthai(monkeypatch):
    monkeypatch.setattr(camera, "usb_luxonis_present", lambda: False)
    monkeypatch.setattr(camera, "list_oak_devices", lambda: (_ for _ in ()).throw(AssertionError("nie wołać")))
    ok, _ = camera.oak_present()
    assert ok is False
