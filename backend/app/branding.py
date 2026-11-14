"""Logotypy w stopce: pliki z assets/ albo białe placeholdery SVG w tym samym stylu."""
from __future__ import annotations

from pathlib import Path

LOGOS = {
    "kolo": ("logo_kola", "logo_kolo", "logo-kola", "logo-kolo"),
    "rzeszow": ("logo_rzeszow", "logo-rzeszow"),
}
EXTS = (".svg", ".png", ".webp", ".jpg", ".jpeg")

_FONT = "font-family=\"'Barlow Condensed','Arial Narrow',Arial,sans-serif\""

PLACEHOLDERS = {
    "kolo": f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 360 120" width="360" height="120">
  <g fill="none" stroke="#f3f4f6" stroke-width="3" opacity=".92">
    <circle cx="60" cy="60" r="50"/>
    <path d="M28 34 L60 22 L92 34 M28 34 L60 60 L92 34 M28 86 L60 60 L92 86 M28 86 L60 98 L92 86 M28 34 L28 86 M92 34 L92 86"/>
  </g>
  <g fill="#f3f4f6">
    <circle cx="28" cy="34" r="6"/><circle cx="60" cy="22" r="6"/><circle cx="92" cy="34" r="6"/>
    <circle cx="60" cy="60" r="7"/><circle cx="28" cy="86" r="6"/><circle cx="60" cy="98" r="6"/>
    <circle cx="92" cy="86" r="6"/>
  </g>
  <g fill="#f3f4f6" {_FONT}>
    <text x="128" y="42" font-size="22" font-weight="500" letter-spacing="1">KOŁO NAUKOWE</text>
    <text x="128" y="78" font-size="38" font-weight="700" letter-spacing="0.5">MACHINE LEARNING</text>
    <text x="128" y="102" font-size="17" font-weight="600" letter-spacing="1">POLITECHNIKI RZESZOWSKIEJ</text>
  </g>
</svg>""",
    "rzeszow": f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 120" width="400" height="120">
  <rect x="4" y="8" width="112" height="104" rx="18" fill="none" stroke="#f3f4f6" stroke-width="4"/>
  <path d="M60 22 L74 52 L104 60 L74 68 L60 98 L46 68 L16 60 L46 52 Z" fill="#f3f4f6"/>
  <g fill="#f3f4f6" {_FONT}>
    <text x="136" y="66" font-size="56" font-weight="600" letter-spacing="2">rzeszów</text>
    <text x="138" y="100" font-size="26" font-weight="500" letter-spacing="1.5">stolica innowacji</text>
  </g>
</svg>""",
}


def find_logo(assets_dir: str, name: str) -> Path | None:
    base = Path(assets_dir)
    for stem in LOGOS.get(name, ()):
        for ext in EXTS:
            p = base / f"{stem}{ext}"
            if p.is_file():
                return p
    return None
