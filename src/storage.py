"""Small file-writing primitives shared by public outputs."""

import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a UTF-8 text file, cleaning only the temporary file we created."""
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(content)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
