"""Data models for the multi-agent RCA system."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Hypothesis(BaseModel):
    """A single failure hypothesis produced by a domain agent."""

    pattern_id: str = ""
    description: str
    confidence: str = "medium"  # high | medium | low
    reasoning: str = ""
    evidence_summary: str = ""


class DomainFinding(BaseModel):
    """Output produced by each domain agent after analysing its slice of data."""

    domain: str
    status: str = "healthy"  # healthy | degraded | failed
    summary: str = ""
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    affected_components: list[str] = Field(default_factory=list)
    event_count: int = 0
    match_count: int = 0
    key_evidence: list[dict] = Field(default_factory=list)


DOMAIN_INFRASTRUCTURE = "infrastructure"
DOMAIN_NETWORK = "network"
DOMAIN_KUBERNETES = "kubernetes"
DOMAIN_JUJU = "juju"
DOMAIN_STORAGE = "storage"
DOMAIN_PIPELINE = "pipeline"
DOMAIN_OBSERVABILITY = "observability"

ALL_DOMAINS = [
    DOMAIN_INFRASTRUCTURE,
    DOMAIN_NETWORK,
    DOMAIN_KUBERNETES,
    DOMAIN_JUJU,
    DOMAIN_STORAGE,
    DOMAIN_OBSERVABILITY,
    DOMAIN_PIPELINE,
]
