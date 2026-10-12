FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

# ---- образ для тестов: docker compose run --rm tests ----
FROM base AS test
COPY requirements-dev.txt .
RUN pip install --no-cache-dir -r requirements-dev.txt
COPY scripts ./scripts
COPY data ./data
COPY docs.json pytest.ini ./
COPY tests ./tests
CMD ["pytest", "-v"]

# ---- рабочий образ (последний этап = цель по умолчанию) ----
FROM base AS runtime
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
