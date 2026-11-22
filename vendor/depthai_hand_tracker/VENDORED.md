# depthai_hand_tracker (kopia w repozytorium)

- Źródło: https://github.com/geaxgx/depthai_hand_tracker
- Commit: `97731232fffd467e8de2c3a34ab8382962bb385e` (2023-01-14)
- Licencja: MIT (`LICENSE.txt`, © 2021 geax)

Skopiowane są tylko pliki potrzebne w trybie **Edge, solo, model landmarków lite**:

| Plik | SHA-256 |
|---|---|
| `HandTrackerEdge.py`, `mediapipe_utils.py`, `FPS.py`, `template_manager_script_solo.py` | kod |
| `models/palm_detection_sh4.blob` | `4d45ecfddc68d8365fef3cb633d78b916612f25f7a48898f90f8a47d4b151aa5` |
| `models/hand_landmark_lite_sh4.blob` | `98f2e9642099a572c96e80af944212efec199cdea1995ef391fab0d3a9da45fd` |
| `custom_models/PDPostProcessing_top2_sh1.blob` | `ef4c2db0bd6b4d3209abcc90fefc54e6d23b614c5165774ac1b706a7652c6516` |

## Zmiany względem oryginału

1. `HandTrackerEdge.py`: zakomentowany nieużywany import `from numpy.lib.arraysetops import isin`
   (moduł nie istnieje w numpy 2.x; obraz i tak przypina numpy 1.26).
2. `template_manager_script_solo.py` (skrypt na VPU): z dwóch dłoni wykrytych przez detektor brana jest
   **większa, czyli bliższa kamery**, a nie tylko ta z najwyższą pewnością (oryginał: `detection[:8]`).
   Dłoń opuszczona dalej od kamery nie przejmuje sterowania; potem tryb solo śledzi wybraną dłoń, dopóki jej nie zgubi.

Nic więcej nie zmieniano. Kod jest używany przez `backend/app/hw/camera_worker.py`
(klasa `HandTracker` z `HandTrackerEdge.py`, `xyz=True`, `use_gesture=True`).
Modele `full` i `sparse` nie są dołączone – `OAK_LM_MODEL` inny niż `lite` wymaga dogrania pliku
`models/hand_landmark_<model>_sh4.blob` (bez niego kamera przejdzie w tryb zapasowy).
