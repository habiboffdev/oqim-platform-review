FROM python:3.13-slim

WORKDIR /app

# System deps for asyncpg and cryptography.
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libpq-dev python3-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8001/health')" || exit 1

# Production: no reload, 2 workers (adjust based on VM cores)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", "--workers", "2"]
