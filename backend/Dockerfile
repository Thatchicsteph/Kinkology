FROM python:3.11-slim

WORKDIR /app

# System deps for a few Python wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8001"]
