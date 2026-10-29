import logging
import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.videos import pipeline

logger = logging.getLogger("apps.videos")


class Command(BaseCommand):
    help = "Worker que processa os vídeos da fila (extração, tratamento, segmentos e transcrição)."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Processa a fila uma vez e termina.")

    def handle(self, *args, once=False, **options):
        def stop(signum, frame):
            logger.info("Sinal %s recebido: encerrando após devolver o trabalho atual à fila.", signum)
            pipeline.shutdown_requested.set()
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        self.stdout.write("Worker de vídeos iniciado.")
        current = None
        try:
            while True:
                close_old_connections()
                pipeline.recover_stale()
                current = pipeline.claim_next()
                if current is None:
                    if once:
                        return
                    time.sleep(settings.VIDEO_WORKER_POLL_SECONDS)
                    continue
                logger.info("Processando vídeo %s (%s).", current.pk, current.title)
                pipeline.process(current)
                current = None
        finally:
            if current is not None and pipeline.shutdown_requested.is_set():
                pipeline.requeue_interrupted(current)
