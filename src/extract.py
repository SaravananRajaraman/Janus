"""Content extraction.

Policy (per the design doc):
  .txt / .md / .csv  → read full text, truncate to MAX_CHARS
  everything else     → return None  (analyze node uses filename + metadata only)

Truncation takes the first half + last half so local models see both
the opening context and the tail of longer files.
"""
from __future__ import annotations

from pathlib import Path

TEXT_EXTENSIONS: frozenset[str] = frozenset({".txt", ".md", ".csv"})
MAX_CHARS: int = 4_000
_TRUNCATION_MARKER: str = "\n…[truncated]…\n"


def extract_content(path: str | Path, max_chars: int = MAX_CHARS) -> str | None:
    """Return extracted text for supported types; None for everything else.

    Args:
        path:      Absolute (or relative) path to the file.
        max_chars: Maximum characters to return.  Defaults to MAX_CHARS (4 000).

    Returns:
        A string (possibly truncated) for text types, None otherwise.
    """
    p = Path(path)

    if p.suffix.lower() not in TEXT_EXTENSIONS:
        return None

    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except (OSError, PermissionError):
        return None

    if len(text) <= max_chars:
        return text

    # Take first half + last half to preserve both opening context and tail.
    half = (max_chars - len(_TRUNCATION_MARKER)) // 2
    return text[:half] + _TRUNCATION_MARKER + text[-half:]
