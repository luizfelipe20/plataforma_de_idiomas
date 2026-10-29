FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# FFmpeg: extração, tratamento e segmentação do áudio dos vídeos.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Modelo do RNNoise para fala com ruído de gravação ("somnolent-hogwash"), fixado num
# commit e conferido pelo SHA-256. Os modelos não estão sujeitos a direitos autorais
# (README de GregorR/rnnoise-models).
ARG RNNOISE_URL=https://raw.githubusercontent.com/GregorR/rnnoise-models/3eee541a283fd3b8f81b85b1748e3b9ccbefa04d/somnolent-hogwash-2018-09-01/sh.rnnn
ARG RNNOISE_SHA256=70bb6685eb0c2a1d18e2918dca3fbfbd39317010b1802eb1b6ea73a92f3fdec0
RUN mkdir -p /opt/rnnoise && python -c "import hashlib, sys, urllib.request; \
data = urllib.request.urlopen(sys.argv[1], timeout=60).read(); \
assert hashlib.sha256(data).hexdigest() == sys.argv[2], 'checksum do modelo RNNoise não confere'; \
open('/opt/rnnoise/sh.rnnn', 'wb').write(data)" "$RNNOISE_URL" "$RNNOISE_SHA256"

COPY requirements.txt requirements-prod.txt ./
RUN pip install -r requirements-prod.txt

COPY . .

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /app/staticfiles /app/media /app/models/whisper \
    && chown -R app:app /app
USER app

RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "180", "--access-logfile", "-"]
