"""
Chamadas ao FFmpeg/FFprobe.

Tudo é processado de arquivo para arquivo pelo próprio FFmpeg (streaming), sem
carregar vídeo ou áudio na memória do Python. As funções longas aceitam um
`on_progress(fração)` alimentado pela saída `-progress` do FFmpeg.
"""
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

# Taxa usada no processamento intermediário (o RNNoise trabalha em 48 kHz).
WORK_SAMPLE_RATE = 48000
PLAYBACK_BITRATE = "64k"


class FFmpegError(Exception):
    pass


@dataclass
class ProbeResult:
    duration: float
    format_name: str
    has_video: bool
    has_audio: bool


def probe(path):
    """Lê duração e faixas do arquivo com ffprobe (só os metadados)."""
    cmd = [settings.FFPROBE_BINARY, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except FileNotFoundError as exc:
        raise FFmpegError("FFprobe não está instalado no servidor.") from exc
    if result.returncode != 0:
        raise FFmpegError("O arquivo não é um vídeo válido ou está corrompido.")
    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    try:
        duration = float(fmt.get("duration") or 0)
    except ValueError:
        duration = 0.0
    return ProbeResult(
        duration=duration,
        format_name=fmt.get("format_name", ""),
        has_video=any(s.get("codec_type") == "video" and s.get("disposition", {}).get("attached_pic") != 1 for s in streams),
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def run(args, *, duration=None, on_progress=None):
    """Executa o FFmpeg; com `duration` e `on_progress`, informa a fração concluída."""
    cmd = [settings.FFMPEG_BINARY, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    if on_progress and duration:
        cmd += ["-progress", "pipe:1", "-nostats"]
    cmd += args
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except FileNotFoundError as exc:
        raise FFmpegError("FFmpeg não está instalado no servidor.") from exc
    if on_progress and duration:
        for line in proc.stdout:
            key, _, value = line.strip().partition("=")
            if key == "out_time_us" and value.isdigit():
                on_progress(min(1.0, int(value) / 1_000_000 / duration))
    _, stderr = proc.communicate()
    if proc.returncode != 0:
        logger.warning("FFmpeg falhou (%s): %s", proc.returncode, stderr[-2000:])
        raise FFmpegError(f"FFmpeg falhou: {stderr.strip()[-300:] or 'erro desconhecido'}")


def extract_audio(video_path, wav_path, *, duration=None, on_progress=None):
    """Primeira faixa de áudio do vídeo, em WAV mono 48 kHz (sem perdas, para tratar depois)."""
    run(
        ["-i", video_path, "-vn", "-map", "0:a:0", "-ac", "1", "-ar", str(WORK_SAMPLE_RATE), "-c:a", "pcm_s16le", wav_path],
        duration=duration, on_progress=on_progress,
    )


def rnnoise_available():
    model = settings.AUDIO_RNNOISE_MODEL
    return bool(model) and os.path.exists(model)


def clean_filter():
    """
    Cadeia de filtros que prioriza a inteligibilidade da fala:

    - highpass/lowpass: corta graves (ronco, batida da música) e agudos fora da faixa da voz;
    - arnndn (RNNoise): rede neural treinada para separar fala de ruído; `mix` < 1 devolve
      parte do sinal original, preservando a voz quando o modelo erra;
    - afftdn: redução de ruído estacionário (chiado) por FFT, moderada;
    - dynaudnorm: equaliza o volume das falas sem estourar.
    """
    filters = ["highpass=f=80", "lowpass=f=8000"]
    if rnnoise_available():
        filters.append(f"arnndn=m='{settings.AUDIO_RNNOISE_MODEL}':mix={settings.AUDIO_RNNOISE_MIX}")
    else:
        logger.info("Modelo RNNoise não encontrado (%s): usando apenas afftdn.", settings.AUDIO_RNNOISE_MODEL)
    filters += ["afftdn=nr=12:nf=-30:tn=1", "dynaudnorm=f=250:g=15:p=0.9"]
    return ",".join(filters)


def clean_audio(wav_in, wav_out, *, duration=None, on_progress=None):
    run(["-i", wav_in, "-af", clean_filter(), "-ar", str(WORK_SAMPLE_RATE), "-c:a", "pcm_s16le", wav_out],
        duration=duration, on_progress=on_progress)


def encode_mp3(wav_in, mp3_out, *, start=None, length=None, duration=None, on_progress=None):
    """MP3 mono para reprodução; `start`/`length` recortam com precisão de amostra (entrada WAV)."""
    args = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    args += ["-i", wav_in, "-ac", "1", "-c:a", "libmp3lame", "-b:a", PLAYBACK_BITRATE, mp3_out]
    run(args, duration=duration, on_progress=on_progress)


GATE_FADE_SECONDS = 0.25


def gate_expression(intervals, fade=GATE_FADE_SECONDS):
    """
    Ganho do filtro `volume` (t em segundos): 1 dentro dos intervalos de fala, 0 fora,
    com rampas de `fade` s. Os intervalos não se sobrepõem, então a soma vale no máximo 1.
    """
    if not intervals:
        return "0"
    terms = "+".join(
        f"clip((t-{a - fade:.3f})/{fade},0,1)*clip(({b + fade:.3f}-t)/{fade},0,1)" for a, b in intervals
    )
    return f"min(1,{terms})"


def gate_audio(audio_in, mp3_out, intervals, *, start=None, length=None):
    """MP3 de `audio_in` (ou do trecho start/length) com silêncio fora de `intervals`, tempos da saída."""
    args = []
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    if length is not None:
        args += ["-t", f"{length:.3f}"]
    args += ["-i", audio_in, "-af", f"volume='{gate_expression(intervals)}':eval=frame",
             "-ac", "1", "-c:a", "libmp3lame", "-b:a", PLAYBACK_BITRATE, mp3_out]
    run(args)


def cut_clip(audio_in, mp3_out, start, end):
    """
    Recorte de [start, end] (s) de um MP3 já tratado, ou da lista de MP3s consecutivos
    (segmentos) tocados em sequência. Copia os quadros MP3 sem reconverter: mesma qualidade
    do áudio tratado e sem novo processamento (a precisão de ~26 ms por quadro fica coberta
    pela folga do recorte).
    """
    paths = [audio_in] if isinstance(audio_in, str) else list(audio_in)
    listing = None
    if len(paths) == 1:
        source = ["-i", paths[0]]
    else:
        fd, listing = tempfile.mkstemp(suffix=".txt", dir=os.path.dirname(mp3_out))
        with os.fdopen(fd, "w") as fh:
            fh.writelines("file '{}'\n".format(p.replace("'", "'\\''")) for p in paths)
        source = ["-f", "concat", "-safe", "0", "-i", listing]
    start = max(0.0, start)
    try:
        run([*source, "-ss", f"{start:.3f}", "-t", f"{max(0.1, end - start):.3f}",
             "-map", "0:a:0", "-c:a", "copy", mp3_out])
    finally:
        if listing:
            os.remove(listing)
