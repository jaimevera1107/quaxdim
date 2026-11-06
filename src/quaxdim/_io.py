"""Small file-system helpers shared by reports and the CLI."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def write_text_atomic(path: str | Path, text: str) -> None:
    """Write ``text`` to ``path`` so readers never see a partial file.

    The content goes to a temporary file in the same directory and is
    moved into place with ``os.replace``, which is atomic on POSIX and on
    Windows. A crash mid-write leaves the previous file untouched.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, target)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def describe_source(source: object) -> dict[str, object]:
    """Describe a data source for the audit trail without reading it.

    File sources record path, size and modification time, enough to tell
    later whether the same file was evaluated. In-memory frames record
    only their type: the report never stores rows or values.
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        description: dict[str, object] = {
            "kind": "file",
            "path": str(path),
        }
        try:
            stat = path.stat()
        except OSError:
            description["exists"] = False
            return description
        description["size_bytes"] = stat.st_size
        description["modified_at"] = datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat()
        return description
    kind = type(source)
    return {"kind": "in_memory", "type": f"{kind.__module__}.{kind.__name__}"}
