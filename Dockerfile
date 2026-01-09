FROM ghcr.io/astral-sh/uv:python3.13-alpine

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

# Install deps from the lockfile for reproducible environments
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project

# Copy application code last (better layer caching)
COPY main.py /app/main.py
COPY src /app/src

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
