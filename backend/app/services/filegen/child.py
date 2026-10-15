"""Renders one file the chat assistant creates, in a child process (see document_tool.render).

Run as `python -I child.py` with an empty environment and a scratch directory as the working directory. Rendering
is pure-Python CPU work that can't be interrupted in a thread, so it runs here, where a memory, CPU and file-size
limit and a kill end only this process. Imports only the standard library, the filegen package and its rendering
libraries: never app.*, whose settings need secrets this process must not have.

Reads one JSON job from stdin: {"request": RenderRequest as JSON, "out": path, "cpu_seconds", "max_bytes"}. Writes
the file to `out` and prints one JSON line: {"ok": true, "pages": n | null, "notes": [...]}, {"error": message}
for input that can't be rendered, or {"crash": exception type}. Never prints the content.
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
    try:
        import filegen
    except BaseException as exc:
        _emit({"crash": type(exc).__name__})
        return 1
    try:
        rendered = filegen.render(filegen.RenderRequest.from_json(job["request"]))
        Path(job["out"]).write_bytes(rendered.data)
    except filegen.RenderError as exc:
        _emit({"error": str(exc)})
    except OSError as exc:
        _emit({"crash": "FileTooLarge" if exc.errno == errno.EFBIG else type(exc).__name__})
    except BaseException as exc:
        _emit({"crash": type(exc).__name__})
    else:
        _emit({"ok": True, "pages": rendered.pages, "notes": rendered.notes})
    return 0


if __name__ == "__main__":
    sys.exit(main())
