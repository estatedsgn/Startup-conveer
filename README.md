# Startup-Conveer

An autonomous **AI company** built as a *conveyor of agents*. The owner sets
strategy and holds veto power; AI "employees" do the work, organized into
departments. This repo implements **Phase 0**: department #1 —
**idea generation + initial hypothesis testing**.

Each "employee" is a separate **headless Claude** process (`claude -p`) with its
own role prompt and restricted tool scope, orchestrated by **LangGraph** with
human-in-the-loop gates, traced by **LangSmith**.

> Compliance-first: customer outreach is **human-in-the-loop** — the owner
> personally contacts opted-in people. The system never does mass messaging or
> ToS-violating automation. See [docs/charter.md](docs/charter.md).

## Flow (department #1)

```
Idea Generator → [you pick 3] → Analyst (test design + metrics)
→ [you paste custdev results] → Reporter → CEO (go/no-go) → HTML dashboard
```

## Requirements

- Python 3.11+
- The `claude` CLI installed and logged in (or `ANTHROPIC_API_KEY` set).

## Setup

```bash
pip install -e .
cp .env.example .env   # fill in keys
```

Verify the runtime:

```bash
claude -p "ping" --output-format json
```

## Run

```bash
conveer run --topic "B2B SaaS for logistics" --ideas 6
# pick 3 ideas when prompted; optionally paste customer-discovery results
conveer resume <run_id>   # continue a paused run later
```

Artifacts land in `runs/<run_id>/` (`report.html`, `report.md`, `decision.json`, …).
With `LANGSMITH_TRACING=true`, the full run is traced in LangSmith.

## Architecture

The company is layered (orchestration is just one layer): runtime (headless
Claude), orchestration (LangGraph), tools (MCP), agent-to-agent (A2A),
durability (Temporal/Inngest), memory (Mem0/Letta), observability (LangSmith).
Interaction is governed by typed contracts — see [docs/protocol.md](docs/protocol.md).

## Tests

```bash
pytest
```
