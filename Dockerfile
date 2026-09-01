FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system cherryfin && adduser --system --ingroup cherryfin cherryfin

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

USER cherryfin
EXPOSE 8080

CMD ["uvicorn", "cherryfin.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
