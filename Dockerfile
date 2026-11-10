# Use official Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /middleware

# Install system dependencies
RUN apt-get update && apt-get install -y gcc libffi-dev libssl-dev curl ca-certificates tzdata && rm -rf /var/lib/apt/lists/*

# Trust store. pip, requests and httpx verify TLS against certifi, not the
# system store, and check no revocation: a root stays trusted until the bundle
# drops it. pip's own vendored copy here dates from 2023 and still trusts the
# roots behind the eMudhra intermediates whose keys leaked in October 2026.
# Install certifi first, hash-pinned so that this one download cannot be
# tampered with, then make pip use it for everything that follows.
# (`--hash` is only accepted inside a requirements file.)
RUN echo "certifi==2026.10.23 --hash=sha256:e9b54de76e530bb6cf0ccd036787103acb58fd42ba930ea8bf948ddbaa2f61d1" > /tmp/certifi.txt \
 && pip install --no-cache-dir --require-hashes -r /tmp/certifi.txt \
 && rm /tmp/certifi.txt
ENV PIP_CERT=/usr/local/lib/python3.11/site-packages/certifi/cacert.pem

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Start server with log level from config.py
CMD ["uvicorn", "middleware.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
