FROM python:3.12-slim

# Hugging Face Spaces runs the container as user 1000
RUN useradd -m -u 1000 user
WORKDIR /app
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=user . .

USER user
ENV DB_PATH=/tmp/fleetflow.db \
    PYTHONUNBUFFERED=1
EXPOSE 7860

# --preload: create the database and demo data once, before the workers start
# uses the host's $PORT if it sets one (Railway, Koyeb, Fly), else 7860 (Hugging Face)
CMD gunicorn app:app --preload --workers 2 --threads 4 --timeout 120 --bind 0.0.0.0:${PORT:-7860}
