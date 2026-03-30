"""Base class for domain-specific RCA agents.

Each domain agent:
1. Receives the full state but filters events to its domain
2. Runs domain-specific pattern matching on filtered events
3. Calls the LLM with a domain-expert prompt (including raw log snippets)
4. Returns a DomainFinding
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path

from sunbeam_rca.agents.models import DomainFinding, Hypothesis
from sunbeam_rca.agents.router import partition_events, partition_patterns
from sunbeam_rca.analysis.baseline import get_baseline_noise_summary
from sunbeam_rca.analysis.pattern_matcher import match_patterns
from sunbeam_rca.config import get_llm
from sunbeam_rca.models import LogEvent, LogLevel, PatternMatch
from sunbeam_rca.utils.sanitizer import sanitize

logger = logging.getLogger(__name__)

_MAX_SNIPPET_LINES = 15
_MAX_SNIPPETS = 3
_MAX_SNIPPET_CHARS = 3000


class BaseDomainAgent:
    """Base class for all domain agents.

    Subclasses must set ``domain`` and ``system_prompt``.
    They may override ``_enrich_user_prompt`` to inject domain-specific
    context (e.g. Juju status summary for the Juju agent).
    """

    domain: str = ""
    system_prompt: str = ""

    def analyze(
        self,
        events: list[dict],
        patterns: list,
        state: dict,
    ) -> DomainFinding:
        """Run the full domain analysis pipeline."""
        domain_events = partition_events(events).get(self.domain, [])
        domain_patterns = partition_patterns(patterns).get(self.domain, [])

        log_events = [LogEvent(**e) for e in domain_events]
        matches = match_patterns(log_events, domain_patterns) if domain_patterns else []

        finding = self._build_finding(matches, domain_events, state)

        llm = get_llm()
        if llm and (matches or self._should_analyze_without_matches(domain_events)):
            finding = self._llm_analyze(llm, finding, matches, domain_events, state)

        return finding

    def _build_finding(
        self,
        matches: list[PatternMatch],
        events: list[dict],
        state: dict,
    ) -> DomainFinding:
        """Build a DomainFinding from pattern matches (deterministic)."""
        if not matches:
            return DomainFinding(
                domain=self.domain,
                status="healthy",
                summary=f"No known failure patterns detected in {self.domain} domain.",
                event_count=len(events),
                match_count=0,
            )

        freq = Counter(m.pattern_id for m in matches)
        unique_matches = {}
        for m in matches:
            if m.pattern_id not in unique_matches:
                unique_matches[m.pattern_id] = m

        hypotheses = []
        affected = set()
        key_evidence = []

        for pid, m in unique_matches.items():
            count = freq[pid]
            hypotheses.append(Hypothesis(
                pattern_id=pid,
                description=m.description,
                confidence="high" if m.severity >= 8 else "medium" if m.severity >= 6 else "low",
                reasoning=f"Matched {count}x, severity={m.severity}",
                evidence_summary=sanitize(m.matched_event.to_context_str(200)),
            ))
            key_evidence.append({
                "pattern_id": pid,
                "source_file": m.matched_event.source_file,
                "line_number": m.matched_event.line_number,
                "message": m.matched_event.message[:300],
                "frequency": count,
            })

            src = m.matched_event.source_file
            if src:
                affected.add(src.split("/")[-1])

        max_sev = max(m.severity for m in unique_matches.values())
        status = "failed" if max_sev >= 8 else "degraded" if max_sev >= 5 else "healthy"

        summary = (
            f"Found {len(unique_matches)} unique failure pattern(s) "
            f"({len(matches)} total hits) in {self.domain} domain."
        )

        return DomainFinding(
            domain=self.domain,
            status=status,
            summary=summary,
            hypotheses=hypotheses,
            affected_components=sorted(affected),
            event_count=len(events),
            match_count=len(matches),
            key_evidence=key_evidence,
        )

    def _llm_analyze(
        self,
        llm,
        finding: DomainFinding,
        matches: list[PatternMatch],
        events: list[dict],
        state: dict,
    ) -> DomainFinding:
        """Enhance the finding with LLM domain-expert reasoning."""
        user_prompt = self._build_user_prompt(finding, matches, events, state)

        try:
            from langchain_core.messages import HumanMessage, SystemMessage

            sys_prompt = self.system_prompt
            baseline_section = get_baseline_noise_summary()
            if baseline_section:
                sys_prompt += "\n\n" + baseline_section

            resp = llm.invoke([
                SystemMessage(content=sys_prompt),
                HumanMessage(content=user_prompt),
            ])
            analysis = resp.content if hasattr(resp, "content") else str(resp)

            return self._parse_llm_response(finding, analysis)
        except Exception:
            logger.exception("LLM analysis failed for %s agent", self.domain)
            return finding

    def _build_user_prompt(
        self,
        finding: DomainFinding,
        matches: list[PatternMatch],
        events: list[dict],
        state: dict,
    ) -> str:
        """Build the user prompt for the domain LLM call.

        Includes structured pattern matches AND raw log snippets from the
        source files so the LLM can reason about unstructured context the
        parsers may have missed.
        """
        failure_ts = state.get("failure_timestamp", "unknown")

        freq = Counter(m.pattern_id for m in matches)
        seen: set[str] = set()
        parts: list[str] = []
        snippet_matches: list[PatternMatch] = []
        for m in matches:
            if m.pattern_id in seen:
                continue
            seen.add(m.pattern_id)
            count = freq[m.pattern_id]
            ctx = "\n".join(
                sanitize(ce.to_context_str(150)) for ce in m.context_events[:3]
            )
            parts.append(
                f"### {m.pattern_id} (severity={m.severity}, freq={count}x)\n"
                f"{m.description}\n"
                f"Event: {sanitize(m.matched_event.to_context_str())}\n"
                f"Context:\n{ctx}\n"
            )
            snippet_matches.append(m)

        error_events = [
            e for e in events
            if e.get("level") in ("ERROR", "WARNING")
        ]
        error_sample = error_events[:20]
        errors_text = "\n".join(
            sanitize(LogEvent(**e).to_context_str(200)) for e in error_sample
        )

        prompt = (
            f"## Pipeline failure timestamp\n{failure_ts}\n\n"
            f"## Domain: {self.domain}\n"
            f"## Pattern matches ({len(matches)} total, {len(seen)} unique)\n"
            f"{''.join(parts)}\n"
            f"## Error/Warning events in this domain ({len(error_events)} total, "
            f"showing first {len(error_sample)})\n{errors_text}\n"
        )

        raw_section = self._build_raw_log_section(snippet_matches)
        if raw_section:
            prompt += f"\n{raw_section}\n"

        extra = self._enrich_user_prompt(state)
        if extra:
            prompt += f"\n{extra}\n"

        prompt += (
            "\nRespond with JSON only:\n"
            "{\n"
            '  "domain_status": "healthy|degraded|failed",\n'
            '  "summary": "2-3 sentence analysis of this domain",\n'
            '  "root_hypothesis": {\n'
            '    "pattern_id": "...",\n'
            '    "description": "...",\n'
            '    "reasoning": "why this is the root cause, referencing specific log files"\n'
            "  },\n"
            '  "affected_components": ["..."],\n'
            '  "cross_domain_signals": "anything that might affect other domains"\n'
            "}\n"
        )
        return prompt

    @staticmethod
    def _read_raw_snippet(
        source_file: str, line_number: int, context: int = _MAX_SNIPPET_LINES
    ) -> str:
        """Read raw lines from a log file around a matched line number.

        Returns numbered lines suitable for inclusion in an LLM prompt.
        """
        try:
            path = Path(source_file)
            if not path.exists() or not path.is_file():
                return ""
            with open(path, errors="replace") as f:
                lines = f.readlines()
            start = max(0, line_number - context - 1)
            end = min(len(lines), line_number + context)
            snippet = lines[start:end]
            text = "".join(
                f"{start + i + 1:>6}| {l}" for i, l in enumerate(snippet)
            )
            return text[:_MAX_SNIPPET_CHARS]
        except Exception:
            return ""

    def _build_raw_log_section(self, matches: list[PatternMatch]) -> str:
        """Build a raw-log-snippets section from top pattern matches.

        Sends unprocessed log lines around each match to the LLM so it can
        spot context our parsers may have missed.
        """
        snippets: list[str] = []
        seen_files: set[str] = set()
        for m in matches[:_MAX_SNIPPETS]:
            src = m.matched_event.source_file
            ln = m.matched_event.line_number
            if not src or src in seen_files:
                continue
            seen_files.add(src)
            raw = self._read_raw_snippet(src, ln)
            if raw:
                short = Path(src).name
                snippets.append(
                    f"### Raw log: {short} (around line {ln})\n"
                    f"```\n{raw}\n```\n"
                )
        if not snippets:
            return ""
        return (
            "## Raw log context (unprocessed lines from source files)\n"
            "Use these raw lines for deeper analysis — they may contain "
            "details the structured parser missed.\n\n"
            + "\n".join(snippets)
        )

    def _enrich_user_prompt(self, state: dict) -> str:
        """Override in subclasses to add domain-specific context."""
        return ""

    def _should_analyze_without_matches(self, events: list[dict]) -> bool:
        """Override to allow LLM analysis even without pattern matches."""
        error_count = sum(
            1 for e in events if e.get("level") in ("ERROR", "WARNING")
        )
        return error_count >= 5

    def _parse_llm_response(
        self, finding: DomainFinding, response: str
    ) -> DomainFinding:
        """Parse LLM JSON response and merge into the finding."""
        try:
            clean = response.strip()
            if clean.startswith("```"):
                clean = clean.split("\n", 1)[1]
                clean = clean.rsplit("```", 1)[0]
            data = json.loads(clean)

            finding.status = data.get("domain_status", finding.status)
            finding.summary = data.get("summary", finding.summary)

            root = data.get("root_hypothesis")
            if root and root.get("pattern_id"):
                for h in finding.hypotheses:
                    if h.pattern_id == root["pattern_id"]:
                        h.reasoning = root.get("reasoning", h.reasoning)
                        h.confidence = "high"
                        break
                else:
                    finding.hypotheses.insert(0, Hypothesis(
                        pattern_id=root.get("pattern_id", ""),
                        description=root.get("description", ""),
                        confidence="high",
                        reasoning=root.get("reasoning", ""),
                    ))

            extra_components = data.get("affected_components", [])
            existing = set(finding.affected_components)
            for c in extra_components:
                if c not in existing:
                    finding.affected_components.append(c)

            cross = data.get("cross_domain_signals", "")
            if cross:
                finding.summary += f" Cross-domain: {cross}"

        except (json.JSONDecodeError, AttributeError):
            logger.warning("Could not parse LLM response for %s agent", self.domain)

        return finding
