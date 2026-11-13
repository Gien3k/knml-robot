# syntax=docker/dockerfile:1
# =============================================================================
#  KN ML · ADAS 4WD Demo – obraz wieloarchitekturowy (x86_64 laptop / arm64 RPi 5)
# =============================================================================

# ---------- Etap 1: build frontendu (Vite + TypeScript + Tailwind) ----------
FROM node:22-bookworm-slim AS frontend
WORKDIR /fe
COPY frontend/package.json ./
# --no-package-lock: binarki natywne (rollup / lightningcss / tailwind oxide)
# dobierają się do architektury budującej maszyny – działa tak samo na RPi i PC.
RUN npm install --no-audit --no-fund --no-package-lock
COPY frontend/ ./
RUN npm run build

# ---------- Etap 2: runtime (Python + sterowniki sprzętu) ----------
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libusb – OAK-D (DepthAI); usbutils/v4l-utils – diagnostyka (lsusb, v4l2-ctl)
RUN apt-get update && apt-get install -y --no-install-recommends \
        libusb-1.0-0 libglib2.0-0 usbutils v4l-utils curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt

# DepthAI instalowane osobno i "miękko": gdyby kiedyś zabrakło koła dla danej
# platformy, obraz i tak się zbuduje, a OAK-D przejdzie w tryb symulacji.
RUN pip install "depthai>=2.28,<3" || echo "WARN: depthai niezainstalowane – OAK-D bedzie symulowane"

# Model detekcji osób (MobileNet-SSD, VPU Myriad X) – pobierany podczas budowania,
# więc na stoisku nie potrzeba internetu.
ARG MODEL_URL=https://artifacts.luxonis.com/artifactory/luxonis-depthai-data-local/network/mobilenet-ssd_openvino_2021.4_6shave.blob
RUN mkdir -p /app/models \
    && (curl -fsSL --retry 3 --max-time 180 -o /app/models/mobilenet-ssd.blob "$MODEL_URL" \
        || (rm -f /app/models/mobilenet-ssd.blob; echo "WARN: model niepobrany – OAK bez sieci NN"))

COPY backend/app ./app
COPY --from=frontend /fe/dist ./static

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1

CMD ["python", "-m", "app"]
