# Playwright's image ships Chromium and its system libraries (amd64 and arm64).
# Keep this version equal to playwright in requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.65.2-noble

# TINI_SUBREAPER: tini also reaps orphans when it is not PID 1 (compose `init: true`).
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    TINI_SUBREAPER=1 \
    DATA_DIR=/data \
    DISPLAY=:99 \
    TZ=Europe/Amsterdam

# Xvfb: virtual screen for the headful browser. x11vnc + noVNC: the login browser in your own browser.
# tini reaps the Chromium processes that every run leaves behind.
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb x11vnc novnc tini tzdata fonts-noto-color-emoji \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --break-system-packages -r /app/requirements.txt

COPY app/ /app/
WORKDIR /app

VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=5)" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "-u", "/app/main.py"]
