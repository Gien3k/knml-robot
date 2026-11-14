"""Kiosk po aktualizacji nie może pokazywać starego ekranu z pamięci podręcznej przeglądarki."""
from tests.test_api import client  # noqa: F401  (wspólny serwer testowy)


def test_static_files_are_revalidated(client):  # noqa: F811
    for path in ("/", "/app.js", "/styles.css", "/shared/kn.js", "/shared/base.css"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert r.headers.get("cache-control") == "no-cache", path


def test_main_screen_is_style_one(client):  # noqa: F811
    html = client.get("/").text
    assert 'id="cam"' in html and 'id="radar"' in html and 'id="chassis"' in html
    assert 'type="module" src="app.js"' in html
    css = client.get("/styles.css").text
    assert "height: 134px" in css          # stopka jak na drugim monitorze stoiska
