FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    REPO_PATH=/repo \
    HOST=0.0.0.0 \
    PORT=8000 \
    ALLOW_MUTATIONS=false \
    MAX_READ_BYTES=262144 \
    MAX_OUTPUT_CHARS=200000 \
    ALLOW_ORIGINS=*

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py ./

EXPOSE 8000

CMD ["python", "server.py"]
