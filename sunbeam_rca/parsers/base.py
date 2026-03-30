"""Abstract base for log parsers."""

from __future__ import annotations

import abc
from pathlib import Path

from sunbeam_rca.models import LogEvent


class BaseParser(abc.ABC):
    """All parsers accept a file path and yield ``LogEvent`` objects."""

    @abc.abstractmethod
    def parse(self, file_path: str) -> list[LogEvent]:
        """Parse *file_path* and return a list of structured events."""

    def _read_lines(self, file_path: str) -> list[str]:
        p = Path(file_path)
        if not p.is_file():
            return []
        return p.read_text(errors="replace").splitlines()
