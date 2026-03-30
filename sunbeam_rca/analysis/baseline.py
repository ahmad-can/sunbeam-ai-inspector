"""Baseline profile loader and scoring adjustment calculator.

Loads the baked-in baseline.json (generated from a successful run) and
provides functions to compute scoring adjustments for pattern matches.
Patterns present in the baseline are bootstrap noise; patterns absent
from the baseline are novel to the failure and get a scoring boost.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

_BASELINE_PATH = Path(__file__).parent / "baseline.json"

BASELINE_NOVEL_BONUS = 0.30
BASELINE_COMMON_PENALTY = 0.25


@lru_cache(maxsize=1)
def load_baseline() -> dict:
    """Load and cache the baseline profile from baseline.json."""
    try:
        with open(_BASELINE_PATH) as f:
            data = json.load(f)
        logger.debug(
            "Loaded baseline with %d pattern IDs",
            len(data.get("pattern_ids", {})),
        )
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("Could not load baseline.json — baseline disabled")
        return {}


def is_baseline_pattern(pattern_id: str, baseline: dict | None = None) -> bool:
    """Check if a pattern ID exists in the baseline (i.e., is normal noise)."""
    if baseline is None:
        baseline = load_baseline()
    return pattern_id in baseline.get("pattern_ids", {})


def compute_baseline_adjustment(
    pattern_id: str, baseline: dict | None = None
) -> tuple[float, float, str]:
    """Compute scoring adjustment for a pattern based on the baseline.

    Returns (bonus, penalty, reason):
    - Novel patterns (absent from baseline): bonus > 0, penalty = 0
    - Common patterns (present in baseline): bonus = 0, penalty > 0
    - LLM_DISCOVERED_* patterns are always treated as novel
    """
    if baseline is None:
        baseline = load_baseline()

    if not baseline:
        return 0.0, 0.0, ""

    if pattern_id.startswith("LLM_DISCOVERED_"):
        return BASELINE_NOVEL_BONUS, 0.0, "baseline_novel(llm_discovery)"

    pattern_ids = baseline.get("pattern_ids", {})

    if pattern_id in pattern_ids:
        note = pattern_ids[pattern_id].get("note", "bootstrap noise")
        return 0.0, BASELINE_COMMON_PENALTY, f"baseline_noise({note[:40]})"

    return BASELINE_NOVEL_BONUS, 0.0, "baseline_novel"


def get_baseline_noise_summary() -> str:
    """Build a text summary of baseline noise patterns for LLM prompts."""
    baseline = load_baseline()
    if not baseline:
        return ""

    pattern_ids = baseline.get("pattern_ids", {})
    if not pattern_ids:
        return ""

    lines = [
        "## Known bootstrap noise (also present in successful runs)",
        "The following patterns fire during NORMAL successful Sunbeam builds.",
        "IGNORE them unless they appear at significantly higher frequency",
        "or in unusual contexts. Focus on what is NOT in this list.\n",
    ]
    for pid, info in pattern_ids.items():
        note = info.get("note", "")
        lines.append(f"- {pid}: {note}")

    return "\n".join(lines)
