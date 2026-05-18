# Easy Collector - Dockerfile for Cloud Run
# Build context: easyCollector/ (use "gcloud builds submit ./easyCollector")
# See deploy-collector.sh: copies data/watchlist/0dte_list.csv into easyCollector first.

FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt /tmp/easycollector-requirements.txt
RUN pip install --no-cache-dir -r /tmp/easycollector-requirements.txt

# Create data directory and copy 0DTE symbol list (deploy script copies from parent)
RUN mkdir -p ./data/watchlist
COPY data/watchlist/0dte_list.csv ./data/watchlist/0dte_list.csv

# Copy application code
COPY backend/ ./backend/

ENV PYTHONPATH=/app/backend
ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1
