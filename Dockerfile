# TruthLens AI - container image for Hugging Face Spaces (or any Docker host).
#
# Configure with environment variables / Space secrets (see .env.example):
#   LLM_PROVIDER + LLM_API_KEY   hosted model (no Ollama in the container)
#   DATABASE_URL                 Postgres for history (the container disk is wiped on restart)
#   GOOGLE_/GITHUB_CLIENT_ID/SECRET, SESSION_SECRET, PUBLIC_URL   sign-in
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Hugging Face Spaces runs containers as uid 1000.
RUN useradd --create-home --uid 1000 user
WORKDIR /app

COPY --chown=user requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=user . .
USER user

ENV HOST=0.0.0.0 \
    PORT=7860
EXPOSE 7860

# One worker only: in-progress runs live in the server process's memory.
# --proxy-headers lets the app see the public https URL behind the host's proxy.
CMD ["sh", "-c", "uvicorn server:app --host \"$HOST\" --port \"$PORT\" --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
