"""Load and apply noise filters to suppress transient/benign errors."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_DEFAULT_FILTERS_FILE = Path(__file__).parent / "noise_filters.yaml"


class NoiseFilter:
    """A compiled noise filter entry."""

    __slots__ = ("id", "regex", "penalty", "reason")

    def __init__(self, id: str, regex: re.Pattern, penalty: float, reason: str):
        self.id = id
        self.regex = regex
        self.penalty = penalty
        self.reason = reason


def load_noise_filters(
    path: str | Path | None = None,
) -> list[NoiseFilter]:
    """Load noise filters from YAML."""
    p = Path(path) if path else _DEFAULT_FILTERS_FILE
    if not p.is_file():
        return []
    raw = yaml.safe_load(p.read_text())
    if not raw:
        return []
    filters = []
    for entry in raw:
        try:
            filters.append(
                NoiseFilter(
                    id=entry["id"],
                    regex=re.compile(entry["regex"], re.IGNORECASE),
                    penalty=float(entry.get("penalty", 0.15)),
                    reason=entry.get("reason", ""),
                )
            )
        except (KeyError, re.error) as exc:
            logger.warning("Skipping invalid noise filter: %s", exc)
    return filters


def compute_noise_penalty(message: str, filters: list[NoiseFilter] | None = None) -> float:
    """Return the total noise penalty for a message.

    If multiple filters match, only the highest penalty is applied.
    """
    if filters is None:
        filters = load_noise_filters()
    max_penalty = 0.0
    for f in filters:
        if f.regex.search(message):
            max_penalty = max(max_penalty, f.penalty)
    return max_penalty
