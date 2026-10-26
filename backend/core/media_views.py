import os
import re
import mimetypes
from django.http import HttpResponse, StreamingHttpResponse, FileResponse, Http404

def ranged_media_serve(request, path, document_root=None):
    """
    HTTP Byte-Range supporting media serve view for local development (DEBUG=True).
    Enables HTML5 video scrub/seek (backward/forward 10s) and multi-range requests
    matching production S3/CloudFront partial content behavior.
    """
    if not document_root:
        raise Http404("Document root not configured")

    # Safe path resolution
    doc_root = os.path.abspath(document_root)
    fullpath = os.path.abspath(os.path.join(doc_root, path))
    if not fullpath.startswith(doc_root) or not os.path.exists(fullpath) or os.path.isdir(fullpath):
        raise Http404("File not found")

    file_size = os.path.getsize(fullpath)
    range_header = request.headers.get("Range") or request.META.get("HTTP_RANGE", "")
    content_type, _ = mimetypes.guess_type(fullpath)
    content_type = content_type or "application/octet-stream"

    # No range requested: return full file with Accept-Ranges declared
    if not range_header:
        f = open(fullpath, "rb")
        resp = FileResponse(f, content_type=content_type)
        resp["Accept-Ranges"] = "bytes"
        resp["Content-Length"] = str(file_size)
        return resp

    # Parse range header: e.g. "bytes=1000-2000" or "bytes=1000-"
    m = re.match(r"bytes=(\d+)-(\d*)", range_header.strip())
    if not m:
        f = open(fullpath, "rb")
        resp = FileResponse(f, content_type=content_type)
        resp["Accept-Ranges"] = "bytes"
        resp["Content-Length"] = str(file_size)
        return resp

    start = int(m.group(1))
    end = int(m.group(2)) if m.group(2) else file_size - 1

    if start >= file_size or end >= file_size or start > end:
        resp = HttpResponse(status=416)
        resp["Content-Range"] = f"bytes */{file_size}"
        resp["Accept-Ranges"] = "bytes"
        return resp

    length = end - start + 1

    def file_iterator(file_path, offset, req_len, chunk_size=65536):
        with open(file_path, "rb") as f:
            f.seek(offset)
            remaining = req_len
            while remaining > 0:
                chunk = f.read(min(chunk_size, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    resp = StreamingHttpResponse(
        file_iterator(fullpath, start, length),
        status=206,
        content_type=content_type,
    )
    resp["Content-Range"] = f"bytes {start}-{end}/{file_size}"
    resp["Accept-Ranges"] = "bytes"
    resp["Content-Length"] = str(length)
    return resp
