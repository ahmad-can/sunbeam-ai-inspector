"""Shared Pydantic models for the RCA system."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class LogLevel(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"
    DEBUG = "DEBUG"
    UNKNOWN = "UNKNOWN"


class SourceType(str, Enum):
    PIPELINE = "pipeline"
    SYSLOG = "syslog"
    JUJU = "juju"
    DMESG = "dmesg"
    CLOUD_INIT = "cloud_init"
    KUBERNETES = "kubernetes"
    SUNBEAM = "sunbeam"


class LogEvent(BaseModel):
    """A single parsed log line from any source."""

    timestamp: datetime
    source_file: str
    line_number: int
    level: LogLevel = LogLevel.UNKNOWN
    message: str
    source_type: SourceType
    metadata: dict = Field(default_factory=dict)

    def to_context_str(self, max_msg_len: int = 300) -> str:
        msg = self.message[:max_msg_len]
        return (
            f"[{self.timestamp.isoformat()}] "
            f"{self.source_type.value}:{self.source_file}:{self.line_number} "
            f"{self.level.value} {msg}"
        )


class Evidence(BaseModel):
    """A log line cited as evidence for a root-cause candidate."""

    source_file: str
    line_number: int
    timestamp: datetime
    message: str
    source_type: str


class PatternMatch(BaseModel):
    """A single pattern hit against a log event."""

    pattern_id: str
    category: str
    description: str
    severity: int
    matched_event: LogEvent
    context_events: list[LogEvent] = Field(default_factory=list)


class RootCauseCandidate(BaseModel):
    """A scored root-cause hypothesis with supporting evidence."""

    rank: int = 0
    pattern_id: str
    category: str
    description: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[Evidence] = Field(default_factory=list)
    timeline_start: str = ""
    timeline_end: str = ""
    explanation: str = ""
    llm_reasoning: str = ""


class FailurePattern(BaseModel):
    """A known failure signature loaded from the YAML pattern library."""

    id: str
    category: str
    description: str
    regex: str
    source_types: list[str]
    severity: int = Field(ge=1, le=10)
    context_window_secs: int = 60


class SosReportManifest(BaseModel):
    """Inventory of files discovered inside an sosreport."""

    root_dir: str
    hostname: str = ""
    syslog: str | None = None
    kern_log: str | None = None
    dmesg: str | None = None
    cloud_init_log: str | None = None
    cloud_init_output_log: str | None = None
    juju_logs: list[str] = Field(default_factory=list)
    pod_log_dirs: list[str] = Field(default_factory=list)
    sunbeam_commands: list[str] = Field(default_factory=list)
    kubernetes_commands: list[str] = Field(default_factory=list)
    juju_status_files: list[str] = Field(default_factory=list)
    ovn_logs: list[str] = Field(default_factory=list)
    k8s_cluster_info_logs: list[str] = Field(default_factory=list)
    sunbeam_app_logs: list[str] = Field(default_factory=list)
    juju_models_file: str | None = None
    environment_file: str | None = None
    uname: str | None = None
    meminfo: str | None = None
    df_output: str | None = None


class PipelineManifest(BaseModel):
    """Inventory of files discovered inside a GitHub Actions log archive."""

    root_dir: str
    job_logs: list[str] = Field(default_factory=list)
    system_logs: list[str] = Field(default_factory=list)
