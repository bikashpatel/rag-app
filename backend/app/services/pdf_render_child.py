"""Renders PDF pages to JPEG for the vision model, in a child process (see pdf_images.render_pages).

Run as `python -I pdf_render_child.py` with an empty environment. PDFium can allocate gigabytes or spin on a crafted
page and can't be interrupted, so it runs here, where a memory limit and a kill only ever end this process. Imports
only the standard library, pypdfium2 and PIL: never app.*, whose settings need secrets this process must not have.

Reads one JSON request from stdin: {"pdf", "out_dir", "max_px", "max_dpi", "page_count", "pages": [{"page", "width",
"height", "rotate"}]}. Prints one JSON line per event, flushed: {"start": page} before each page, then
{"page", "ok": true, "width", "height", "bytes"} or {"page", "error": reason}; {"error": "page_count"} if the
document doesn't have the expected number of pages.
"""

import json
import sys
from pathlib import Path
from typing import Any

_RENDER_MEMORY_MB = 1024
_MAX_JPEG_BYTES = 3_500_000
_JPEG_QUALITIES = (85, 60)
_SIZE_TOLERANCE_PT = 1.0


def _emit(event: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def _limit_resources() -> None:
    """Best effort: macOS doesn't enforce RLIMIT_AS, and an unprivileged process may not raise its own limits."""
    try:
        import resource

        limit = _RENDER_MEMORY_MB * 1024 * 1024
        _, hard = resource.getrlimit(resource.RLIMIT_AS)
        if hard != resource.RLIM_INFINITY:
            limit = min(limit, hard)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    except Exception:
        pass
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass
    try:  # Linux: when memory runs out, the kernel kills this process before the worker
        with open("/proc/self/oom_score_adj", "w") as file:
            file.write("1000")
    except OSError:
        pass


def _render(pdf: Any, job: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    number = int(job["page"])
    page = bitmap = None
    try:
        page = pdf[number - 1]
        rotation = page.get_rotation()
        width, height = page.get_size()  # as displayed: swapped for a page rotated by 90 or 270 degrees
        if rotation % 180:
            width, height = height, width
        expected = (abs(float(job["width"])), abs(float(job["height"])))
        if (
            rotation != int(job["rotate"]) % 360
            or abs(abs(width) - expected[0]) > _SIZE_TOLERANCE_PT
            or abs(abs(height) - expected[1]) > _SIZE_TOLERANCE_PT
        ):
            return {"page": number, "error": "mismatch"}
        scale = min(float(request["max_dpi"]) / 72, float(request["max_px"]) / max(width, height, 1.0))
        bitmap = page.render(scale=scale, may_draw_forms=False)
        # Encoded before the bitmap is closed: to_pil() shares PDFium's buffer for some pixel formats.
        image = bitmap.to_pil()
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        path = Path(request["out_dir"]) / f"page-{number}.jpg"
        for quality in _JPEG_QUALITIES:
            image.save(path, "JPEG", quality=quality, optimize=True)
            size = path.stat().st_size
            if size <= _MAX_JPEG_BYTES:
                break
        return {"page": number, "ok": True, "width": image.width, "height": image.height, "bytes": size}
    except Exception as exc:
        return {"page": number, "error": type(exc).__name__}
    finally:
        if bitmap is not None:
            bitmap.close()
        if page is not None:
            page.close()


def main() -> int:
    request = json.loads(sys.stdin.read())
    _limit_resources()
    try:
        import pypdfium2 as pdfium
    except ImportError as exc:
        print(f"pypdfium2 is not installed: {exc}", file=sys.stderr)
        _emit({"error": "import"})
        return 1
    pdf = pdfium.PdfDocument(request["pdf"])
    try:
        if len(pdf) != int(request["page_count"]):
            _emit({"error": "page_count"})
            return 0
        for job in request["pages"]:
            _emit({"start": int(job["page"])})
            _emit(_render(pdf, job, request))
    finally:
        pdf.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
