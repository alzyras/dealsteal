FROM python:3.11-slim

ENV PYTHONFAULTHANDLER=1 \\
    PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY config.example.json ./

RUN pip install --no-cache-dir . \\
    && mkdir -p /app/store

CMD ["dealsteal", "watch", "--interval", "15m"]
