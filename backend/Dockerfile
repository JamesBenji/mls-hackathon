FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
# psycopg[binary], Pillow and reportlab provide wheels; no compiler/libpq needed.
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt \
    && groupadd --gid 10001 guardian \
    && useradd --uid 10001 --gid guardian --no-create-home --shell /usr/sbin/nologin guardian

COPY backend/app ./app
COPY backend/scripts ./scripts
COPY infra/coolify/api-entrypoint.py /usr/local/bin/guardian-entrypoint.py
# Empty named volumes inherit this ownership on first attachment.
RUN mkdir -p /var/lib/guardian/private-photos /app/app/reports_out \
    && chown -R guardian:guardian /var/lib/guardian /app/app/reports_out \
    && chmod 0700 /var/lib/guardian/private-photos /app/app/reports_out

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["python", "/usr/local/bin/guardian-entrypoint.py"]
# APScheduler runs in-process: never add workers, reload, or extra API replicas.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
