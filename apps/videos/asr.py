"""
Reconhecimento automático de fala (ASR).

`get_transcriber()` devolve o transcritor configurado em `ASR_PROVIDER`. O padrão é o
faster-whisper (Whisper rodando localmente em CPU, sem custo por uso e sem enviar o
áudio a terceiros). Os tempos devolvidos são relativos ao arquivo transcrito.
"""
import logging
import threading
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)


class ASRError(Exception):
    pass


@dataclass
class RecognizedCue:
    start: float
    end: float
    text: str


class FasterWhisperTranscriber:
    name = "faster_whisper"
    _model = None
    _lock = threading.Lock()

    @classmethod
    def _load(cls):
        # O modelo é carregado uma vez por processo (o worker) e reaproveitado.
        with cls._lock:
            if cls._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise ASRError("faster-whisper não está instalado (pip install faster-whisper).") from exc
                logger.info("Carregando modelo Whisper %s (%s)", settings.WHISPER_MODEL, settings.WHISPER_COMPUTE_TYPE)
                cls._model = WhisperModel(
                    settings.WHISPER_MODEL,
                    device="cpu",
                    compute_type=settings.WHISPER_COMPUTE_TYPE,
                    cpu_threads=settings.WHISPER_CPU_THREADS,
                    download_root=settings.WHISPER_MODEL_DIR,
                )
            return cls._model

    @staticmethod
    def _decode(path):
        """
        Áudio em float32 mono 16 kHz (o formato do Whisper), decodificado pelo FFmpeg.
        Evita o decodificador PyAV do faster-whisper, sensível a versões da biblioteca.
        Um segmento de 5 min ocupa ~19 MB.
        """
        import subprocess

        import numpy as np

        cmd = [settings.FFMPEG_BINARY, "-nostdin", "-loglevel", "error", "-i", path,
               "-f", "f32le", "-ac", "1", "-ar", "16000", "pipe:1"]
        result = subprocess.run(cmd, capture_output=True, check=False)
        if result.returncode != 0:
            raise ASRError(f"Não foi possível ler o áudio: {result.stderr.decode(errors='ignore')[-200:]}")
        return np.frombuffer(result.stdout, dtype=np.float32)

    def transcribe(self, path, language):
        model = self._load()
        audio = self._decode(path)
        if audio.size == 0:
            return []
        try:
            segments, _info = model.transcribe(
                audio,
                language=language or None,
                vad_filter=True,  # ignora trechos sem fala (música, silêncio)
                vad_parameters={"min_silence_duration_ms": 500},
                beam_size=5,
                condition_on_previous_text=False,  # evita "alucinações" repetidas em trechos de música
            )
            return [
                RecognizedCue(start=float(s.start), end=float(s.end), text=" ".join(s.text.split()))
                for s in segments
                if s.text.strip()
            ]
        except Exception as exc:  # erros internos do modelo ou do decodificador
            raise ASRError(f"Falha na transcrição: {exc}") from exc


class MockTranscriber:
    """Transcrição determinística para testes e desenvolvimento sem o modelo."""

    name = "mock"

    def transcribe(self, path, language):
        from .ffmpeg import probe

        duration = probe(path).duration
        cues, start, n = [], 0.0, 1
        while start < duration - 0.05:
            end = min(duration, start + 4.0)
            cues.append(RecognizedCue(start=start, end=end, text=f"Simulated sentence number {n}."))
            start, n = end, n + 1
        return cues


TRANSCRIBERS = {"faster_whisper": FasterWhisperTranscriber, "mock": MockTranscriber}
_override = None


def get_transcriber():
    if _override is not None:
        return _override
    try:
        return TRANSCRIBERS[settings.ASR_PROVIDER]()
    except KeyError as exc:
        raise ASRError(f"ASR_PROVIDER desconhecido: {settings.ASR_PROVIDER}") from exc


def set_transcriber_override(transcriber):
    """Permite injetar um transcritor (usado nos testes)."""
    global _override
    _override = transcriber
