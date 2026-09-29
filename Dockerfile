# Базовый образ закреплён по хэшу (аудит 29.09.2026): тег 3.14-slim переезжает на новые сборки без
# ведома репозитория. Обновляет Dependabot (экосистема docker) — PR с новым хэшем проходит CI.
FROM python:3.14-slim@sha256:51dafde81dbdb6ebde285137a295cf18a47ca95234fe388a343719cb97305b3d

# PYTHONDONTWRITEBYTECODE: в образе не нужны .pyc — и именно закоммиченный .pyc
# однажды унёс токен бота в публичный репозиторий. PYTHONUNBUFFERED: логи сразу
# в stdout, их забирает json-file драйвер docker.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# sqlite3 — для ручного разбора базы внутри контейнера. gcc больше не ставится:
# у всех зависимостей есть готовые колёса под cp314, а компилятор в рантайм-образе
# — лишние ~100 МБ и лишняя поверхность атаки (аудит, находка M-7).
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Замок с хэшами: транзитивные зависимости те же, что проверил CI (tests/test_lock.py).
COPY requirements.lock .
RUN pip install --require-hashes -r requirements.lock

# Фиксированный UID. Раньше useradd -r выдавал системный UID, а /data монтировался
# с хоста от root — контейнер падал с PermissionError на первой записи (аудит, H-19).
# Теперь /data — именованный том: при первом подключении docker копирует в него
# этот каталог вместе с владельцем, и права верны без ручного chown на хосте.
# /recorder — тот же приём для тома записи стакана (контейнер market-bot-recorder).
RUN groupadd -g 10001 botuser \
    && useradd -u 10001 -g botuser -d /app -s /usr/sbin/nologin botuser \
    && mkdir -p /data/db /data/logs /data/backups /recorder /carry /portfolio /btcalts \
    && chown -R botuser:botuser /app /data /recorder /carry /portfolio /btcalts

COPY --chown=botuser:botuser . .

USER botuser

CMD ["python", "runner.py"]
