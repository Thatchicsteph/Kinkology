FROM python:3.11-slim

WORKDIR /app

# System deps for a few Python wheels + aiortc's native libs (opus, vpx, srtp, ffmpeg)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential pkg-config \
    libopus0 libvpx-dev libsrtp2-dev libssl-dev \
    libavformat-dev libavcodec-dev libavdevice-dev libavutil-dev libswscale-dev libswresample-dev libavfilter-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
