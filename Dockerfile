# Use official Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set work directory
WORKDIR /middleware

# Install system dependencies
RUN apt-get update && apt-get install -y gcc libffi-dev libssl-dev curl ca-certificates tzdata && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 8000

# Start server with log level from config.py
CMD ["uvicorn", "middleware.main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
