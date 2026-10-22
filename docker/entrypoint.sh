#!/bin/sh
# Aplica as migrações antes de iniciar o serviço principal (web).
set -e
if [ "${RUN_MIGRATIONS:-1}" = "1" ]; then
    python manage.py migrate --noinput
fi
exec "$@"
