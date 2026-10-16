"""Renders one file the chat assistant creates, or checks one generated image, in a child process (see
document_tool.render and document_tool.run_child).

Run as `python -I child.py` with an empty environment and a scratch directory as the working directory. Rendering
is pure-Python CPU work that can't be interrupted in a thread, and image data from a model provider is decoded by
C libraries, so both run here, where a memory, CPU and file-size limit and a kill end only this process. Imports
only the standard library, the filegen package and its rendering libraries: never app.*, whose settings need
secrets this process must not have.

Reads one JSON job from stdin and prints one JSON line; never prints the content:
- {"kind": "render" (or no kind), "request": RenderRequest as JSON, "out": path, "cpu_seconds", "max_bytes"}: writes
  the file to `out` (embedded images are read from the working directory) and prints {"ok": true, "pages": n | null,
  "notes": [...]}, {"error": message} for input that can't be rendered, or {"crash": exception type}.
- {"kind": "image", "in": path, "out_dir": path, "creator": the image model, "cpu_seconds", "max_bytes"}: writes
  image.<ext>, preview.webp and thumb.webp to `out_dir` (images.normalize) and prints {"ok": true, "format",
  "width", "height"}, {"error": message} for data that isn't a usable image, or {"crash": exception type}.
"""

import errno
import json
import sys
from pathlib import Path
from typing import Any

_MEMORY_BYTES = 1024 * 1024 * 1024


def _emit(result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(result) + "\n")
    sys.stdout.flush()


def _limit_resources(cpu_seconds: int, max_bytes: int) -> None:
    """Best effort: macOS doesn't enforce RLIMIT_AS, and an unprivileged process may not raise its own limits."""
    try:
        import resource
    except ImportError:
        return
    for name, value in (
        ("RLIMIT_AS", _MEMORY_BYTES),
        ("RLIMIT_CPU", cpu_seconds),
        ("RLIMIT_FSIZE", max_bytes),
        ("RLIMIT_CORE", 0),
    ):
        try:
            limit = getattr(resource, name)
            _, hard = resource.getrlimit(limit)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            resource.setrlimit(limit, (value, value))
        except (OSError, ValueError, AttributeError):
            pass
    try:  # Linux: when memory runs out, the kernel kills this process before the backend
        with open("/proc/self/oom_score_adj", "w") as file:
            file.write("1000")
    except OSError:
        pass


def main() -> int:
    job = json.loads(sys.stdin.buffer.read())
    _limit_resources(int(job["cpu_seconds"]), int(job["max_bytes"]))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    kind = job.get("kind", "render")
    try:
        import filegen

        if kind == "image":
            from filegen import images
    except BaseException as exc:
        _emit({"crash": type(exc).__name__})
        return 1
    try:
        if kind == "image":
            image = images.normalize(Path(job["in"]), Path(job["out_dir"]), creator=str(job["creator"]))
            result: dict[str, Any] = {"ok": True, "format": image.format, "width": image.width, "height": image.height}
        elif kind == "render":
            rendered = filegen.render(filegen.RenderRequest.from_json(job["request"]))
            Path(job["out"]).write_bytes(rendered.data)
            result = {"ok": True, "pages": rendered.pages, "notes": rendered.notes}
        else:
            result = {"crash": "UnknownJob"}
    except filegen.RenderError as exc:
        _emit({"error": str(exc)})
    except OSError as exc:
        _emit({"crash": "FileTooLarge" if exc.errno == errno.EFBIG else type(exc).__name__})
    except BaseException as exc:
        _emit({"crash": type(exc).__name__})
    else:
        _emit(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
