# syntax=docker/dockerfile:1
# =============================================================================
#  KN ML · Autonomiczny Łazik 4WD – jeden obraz dla laptopa (amd64) i Raspberry Pi 5 (arm64)
#  Wszystko, czego potrzeba na stoisku (fonty, JS, modele NN), jest w obrazie – zero internetu.
# =============================================================================
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libusb – OAK-D (DepthAI); usbutils – lsusb do diagnostyki
RUN apt-get update && apt-get install -y --no-install-recommends \
        libusb-1.0-0 libglib2.0-0 usbutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install -r requirements.txt \
    && python -c "import depthai, cv2, numpy, serial, fastapi; print('depthai', depthai.__version__, '| numpy', numpy.__version__, '| opencv', cv2.__version__)"

COPY vendor/ ./vendor/
COPY backend/app ./app
COPY backend/tests ./tests
COPY backend/pytest.ini ./
COPY frontend/ ./static/
COPY assets/ ./assets/

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2)" || exit 1

CMD ["python", "-m", "app"]
