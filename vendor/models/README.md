# Modele sieci neuronowych (offline, w obrazie Dockera)

| Plik | Co to jest | SHA-256 |
|---|---|---|
| `mobilenet-ssd_openvino_2021.4_4shave.blob` | MobileNet-SSD (PASCAL VOC, klasa 15 = osoba), wejście 300×300 BGR, OpenVINO 2021.4, **4 SHAVE** | `1b766c9ff89bd293a4007daf47956ccf11395989d2616bf51314ebe3ce35b25e` |

Źródło: model `mobilenet-ssd` z Open Model Zoo, skompilowany oficjalnym kompilatorem Luxonis:

```python
import blobconverter
blobconverter.from_zoo(name="mobilenet-ssd", shaves=4, version="2021.4", zoo_type="intel")
```

Dlaczego 4 SHAVE: detektor osób działa na VPU razem z hand trackerem (sieci 4 + 4 + 1 SHAVE).
Gotowe pliki Luxonis mają 5 i 6 SHAVE, co mogłoby nie zmieścić się obok śledzenia dłoni.
Modele dłoni są w `vendor/depthai_hand_tracker/` (patrz `VENDORED.md`).
