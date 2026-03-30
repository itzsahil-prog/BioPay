# python/Dockerfile
FROM python:3.11-slim

# dlib build dependencies (required by face_recognition)
RUN apt-get update && apt-get install -y --no-install-recommends \
    cmake \
    build-essential \
    libopenblas-dev \
    liblapack-dev \
    libx11-dev \
    libgtk-3-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create data directories for encrypted embedding files
RUN mkdir -p data/embeddings/face data/embeddings/voice

# Non-root user for security
RUN useradd -m -u 1001 biopay && chown -R biopay:biopay /app
USER biopay

EXPOSE 8001

# Uvicorn with 1 worker (CPU-bound ML tasks don't benefit from multiple)
CMD ["uvicorn", "main:app", \
     "--host", "0.0.0.0", \
     "--port", "8001", \
     "--workers", "1", \
     "--log-level", "info"]
