"""Respostas de arquivo com suporte a Range (avançar/retroceder em players de áudio e vídeo)."""
import mimetypes
import os
import re

from django.http import FileResponse, HttpResponse, StreamingHttpResponse
from django.utils.http import content_disposition_header

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def _iter_range(path, start, length, block=64 * 1024):
    with open(path, "rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            data = fh.read(min(block, remaining))
            if not data:
                break
            remaining -= len(data)
            yield data


def ranged_file_response(request, path, *, content_type=None, filename=None, as_attachment=False, max_age=3600):
    """
    Serve `path` em streaming. Com o cabeçalho Range responde 206 com o trecho pedido
    (o arquivo nunca é carregado inteiro na memória).
    """
    size = os.path.getsize(path)
    content_type = content_type or mimetypes.guess_type(path)[0] or "application/octet-stream"
    match = RANGE_RE.match(request.headers.get("Range", "").strip())
    if match and (match.group(1) or match.group(2)):
        first, last = match.groups()
        if first:
            start = int(first)
            end = min(int(last), size - 1) if last else size - 1
        else:  # sufixo: últimos N bytes
            start, end = max(0, size - int(last)), size - 1
        if start >= size or start > end:
            response = HttpResponse(status=416)
            response["Content-Range"] = f"bytes */{size}"
            return response
        response = StreamingHttpResponse(_iter_range(path, start, end - start + 1), status=206, content_type=content_type)
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
        response["Content-Length"] = str(end - start + 1)
    else:
        response = FileResponse(open(path, "rb"), content_type=content_type)
        response["Content-Length"] = str(size)
    response["Accept-Ranges"] = "bytes"
    response["Cache-Control"] = f"private, max-age={max_age}"
    if filename:
        response["Content-Disposition"] = content_disposition_header(as_attachment, filename)
    return response
