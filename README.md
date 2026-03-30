# Sunbeam AI Inspector

Root-Cause Analysis system for Sunbeam CI build failures. Uses a LangGraph-powered
multi-agent workflow to ingest GitHub Actions pipeline logs and sosreport archives,
run 7 specialized domain agents in parallel, and produce ranked root-cause candidates
with traceable log evidence.

## Features

- **Multi-source log parsing**: Pipeline logs, syslog, dmesg, Juju unit/machine logs, cloud-init, OVN, Kubernetes pod logs, Sunbeam application logs (`sunbeam-*.log`)
- **Juju status analysis**: Parses Juju status JSON to detect stuck units, missing CNI interfaces, SAAS integration issues, and cross-model offer failures
- **Deterministic pattern matching**: 50+ YAML-defined failure signatures with causal chain reasoning
- **7 domain agents**: Specialized AI agents for Infrastructure, Network, Kubernetes, Juju, Storage, Observability, and Pipeline — run in parallel via LangGraph
- **Orchestrator**: Cross-correlates findings from all domain agents into unified hypotheses
- **Deep analysis**: LLM always scans for unknown errors beyond regex patterns, injecting discoveries as synthetic candidates into the scoring pipeline
- **Explainable scoring**: Every confidence score has a human-readable breakdown (severity, timing, frequency, causal chain position, noise penalty, LLM discovery bonus)
- **Failure cascade diagram**: ASCII tree showing the causal chain from root cause to pipeline failure
- **Web UI**: Modern single-page interface with live 8-step pipeline progress via Server-Sent Events
- **Configurable LLM providers**: OpenAI, Anthropic, and Ollama (local) via environment variables

## Local Setup

### Prerequisites

- Python 3.10+
- An API key for OpenAI or Anthropic (or a local Ollama instance)

### 1. Clone and create a virtual environment

```bash
git clone <repository-url>
cd sunbeam-ai-inspector

python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -e ".[dev]"
```

### 3. Configure the LLM provider

```bash
cp .env.example .env
```

Edit `.env` with your chosen provider and API key:

```bash
LLM_PROVIDER=openai          # openai | anthropic | ollama
LLM_MODEL=gpt-4o             # model name for the chosen provider
OPENAI_API_KEY=sk-...         # if using openai
ANTHROPIC_API_KEY=sk-ant-...  # if using anthropic
OLLAMA_BASE_URL=http://localhost:11434  # if using ollama
```

### 4. Run the tests

```bash
pytest
```

## Usage

### CLI

```bash
# Full analysis with both inputs
sunbeam-rca analyze \
  --pipeline ./temp/logs_57061125291.zip \
  --sosreport ./temp/sosreport-host-2026-02-11-abcyxxy.tar.xz \
  --output-dir ./output/

# Pipeline-only analysis (no sosreport)
sunbeam-rca analyze \
  --pipeline ./temp/logs_57061125291.zip \
  --output-dir ./output/

# Sosreport-only analysis (pre-extracted directory)
sunbeam-rca analyze \
  --sosreport ./temp/extracted_sosreport/ \
  --output-dir ./output/

# Verbose logging
sunbeam-rca analyze \
  --pipeline ./temp/logs.zip \
  --sosreport ./temp/sosreport.tar.xz \
  --output-dir ./output/ \
  -v
```

Output in `./output/`:

- `report.json` -- Machine-readable structured report with ranked candidates, evidence, and causal chain diagram
- `report.md` -- Human-readable markdown summary with root cause, failure cascade, and key evidence

### Web UI

Start the web server:

```bash
sunbeam-rca serve
```

This launches the UI at [http://127.0.0.1:8000](http://127.0.0.1:8000).

Options:

```bash
sunbeam-rca serve --host 0.0.0.0 --port 8080
```

The web UI provides:

- **File upload**: Drag-and-drop pipeline `.zip` and sosreport `.tar.xz` files
- **Test run URL**: Paste a `solutions.qa.canonical.com/testruns/...` URL to auto-download artifacts
- **Live progress**: An 8-step pipeline stepper (Collect → Parse → Patterns → Domain Agents → Orchestrate → Deep Analysis → Score → Report) with real-time stats
- **Report display**: Rendered markdown report with failure cascade diagram and expandable ranked candidate cards showing evidence and scoring breakdowns
- **Download**: Export the report as `.json` or `.md`

## LLM Configuration

| Variable | Description | Example |
|---|---|---|
| `LLM_PROVIDER` | Provider name | `openai`, `anthropic`, `ollama` |
| `LLM_MODEL` | Model identifier | `gpt-4o`, `claude-sonnet-4-20250514`, `llama3` |
| `OPENAI_API_KEY` | OpenAI API key | `sk-...` |
| `ANTHROPIC_API_KEY` | Anthropic API key | `sk-ant-...` |
| `OLLAMA_BASE_URL` | Ollama server URL | `http://localhost:11434` |

## Architecture

The system is built as a LangGraph `StateGraph` with 12 nodes (7 agents run in parallel):

```
collect_node → parse_node → pattern_match_node
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────┴─────┐  ┌─────┴─────┐  ┌──────┴──────┐
              │   infra    │  │  network  │  │     k8s     │
              │   agent    │  │   agent   │  │    agent    │
              └─────┬─────┘  └─────┬─────┘  └──────┬──────┘
                    │               │               │
              ┌─────┴─────┐  ┌─────┴─────┐  ┌──────┴──────┐
              │   juju     │  │  storage  │  │ observ-     │
              │   agent    │  │   agent   │  │ ability     │
              └─────┬─────┘  └─────┬─────┘  └──────┬──────┘
                    │               │               │
                    │         ┌─────┴─────┐         │
                    │         │ pipeline  │         │
                    │         │   agent   │         │
                    │         └─────┬─────┘         │
                    │               │               │
                    └───────────────┼───────────────┘
                                    │
                          orchestrator_node
                                    │
                         deep_analyze_node
                                    │
                             score_node
                                    │
                            report_node
```

### Pipeline steps

| Step | Node | What it does |
|------|------|-------------|
| 1 | `collect_node` | Extracts pipeline `.zip` and sosreport `.tar.xz` archives, builds file manifests |
| 2 | `parse_node` | Runs all parsers (pipeline, syslog, dmesg, Juju, cloud-init, OVN, K8s pods, Sunbeam app logs, Juju status JSON, meminfo, df), normalizes timestamps, builds the event timeline |
| 3 | `pattern_match_node` | Scans all events against 50+ YAML-defined regex failure signatures, routes events to domain agents |
| 4 | **Domain agents** (parallel) | 7 specialized AI agents analyze their domain's events via LLM with domain-specific prompts |
| 5 | `orchestrator_node` | Cross-correlates all agent findings into unified hypotheses with LLM reasoning |
| 6 | `deep_analyze_node` | LLM scans failure-window events for unknown errors beyond regex patterns — always runs, injects synthetic candidates |
| 7 | `score_node` | Deterministic scoring and ranking of all candidates (pattern-matched + LLM-discovered) |
| 8 | `report_node` | Generates JSON report and LLM-written concise markdown report |

### Scoring factors

Each root-cause candidate is scored on:

- **Base severity** (0-10, from pattern definition)
- **Failure window bonus** (+0.25 for events within 15 min before failure)
- **Temporal proximity** to the pipeline failure timestamp
- **Pre/post failure timing** (causes before the failure rank higher)
- **Resolved error penalty** (-0.30 for errors that stopped >30 min before failure)
- **Direct failure evidence** (+0.25 for patterns matching the exact failure)
- **Cross-source corroboration** (evidence from multiple log sources)
- **Frequency/persistence** (repeated errors score higher, capped)
- **Causal chain position** (upstream causes boosted, downstream symptoms penalized)
- **LLM root-cause identification** (+0.10 if LLM identifies it as root cause)
- **LLM discovery bonus** (+0.15 for candidates surfaced by deep analysis)
- **Domain agent confidence** (+0.15 for patterns in failed domains)
- **Cross-domain corroboration** (+0.10 for findings confirmed across domains)
- **Noise suppression** (known transient/benign errors are penalized)

## Project Structure

```
sunbeam-auto-triager/
  pyproject.toml
  .env.example
  sunbeam_rca/
    cli.py                          # CLI entry point (analyze + serve)
    config.py                       # LLM provider configuration + retry logic
    state.py                        # RCAState TypedDict (LangGraph state)
    models.py                       # Pydantic models (LogEvent, PatternMatch, etc.)
    graph.py                        # LangGraph StateGraph definition
    nodes/
      collect.py                    # Extract archives, build manifests
      parse.py                      # Run all parsers, build event timeline
      agents.py                     # Multi-agent orchestration (pattern_match, domain agents, orchestrator)
      analyze.py                    # LLM correlation + deep analysis (always-on unknown error discovery)
      score.py                      # Deterministic scoring and ranking
      report.py                     # Generate JSON + markdown reports
    collectors/
      pipeline_collector.py         # GitHub Actions log extraction
      sosreport_collector.py        # Sosreport tarball extraction
    parsers/
      base.py                       # Base parser class
      pipeline_parser.py            # Pipeline log parser
      syslog_parser.py              # Syslog parser
      dmesg_parser.py               # Kernel ring buffer (dmesg) parser
      juju_parser.py                # Juju unit/machine log parser
      juju_status_parser.py         # Juju status JSON parser
      juju_models_parser.py         # Juju models topology parser
      cloud_init_parser.py          # Cloud-init log parser
      k8s_pod_log_parser.py         # Kubernetes pod log parser
      ovn_parser.py                 # OVN/OVS log parser
      sunbeam_log_parser.py         # Sunbeam application log parser
    agents/
      base_agent.py                 # Base domain agent class
      router.py                     # Event routing to domain agents
      models.py                     # Agent-specific Pydantic models
      prompts.py                    # Domain agent LLM prompt templates
      orchestrator.py               # Cross-domain correlation orchestrator
      infra_agent.py                # Infrastructure agent
      network_agent.py              # Network agent
      k8s_agent.py                  # Kubernetes agent
      juju_agent.py                 # Juju agent
      storage_agent.py              # Storage agent
      observability_agent.py        # Observability agent (otel, grafana, prometheus)
      pipeline_agent.py             # Pipeline/test execution agent
    analysis/
      patterns.yaml                 # Failure pattern definitions (50+ signatures)
      pattern_matcher.py            # Regex-based pattern scanner
      causal_chains.py              # Causal relationship graph
      noise_filters.yaml            # Transient/benign error patterns
      noise_filter.py               # Noise penalty calculator
      prompts.py                    # LLM prompt templates (correlation, deep analysis, report)
    utils/
      sanitizer.py                  # Token/secret masking
      timestamps.py                 # Timestamp normalization
    web/
      app.py                        # FastAPI application
      api.py                        # API routes (analyze, stream, report)
      downloader.py                 # Test run artifact downloader
      static/
        index.html                  # Single-page UI
        style.css                   # Styling (dark mode support)
        app.js                      # Frontend logic (SSE, rendering)
        favicon.svg                 # App icon
  tests/
    ...
```
