# Startup-Conveer

An autonomous **AI company** built as a *conveyor of agents*. The owner sets
strategy and holds veto power; AI "employees" do the work, organized into
departments. This repo implements **department #1** —
**idea generation + initial hypothesis testing** — and a **Phase 1 venture
studio** that turns any idea the CEO green-lights into a concrete execution plan.

Each "employee" is a separate **headless Claude** process (`claude -p`) with its
own role prompt and restricted tool scope, orchestrated by **LangGraph** with
human-in-the-loop gates, traced by **LangSmith**.

> Compliance-first: customer outreach is **human-in-the-loop** — the owner
> personally contacts opted-in people. The system never does mass messaging or
> ToS-violating automation. See [docs/charter.md](docs/charter.md).

## Flow

**Department #1 — validation**

```
Idea Generator → [you pick 3] → Analyst (test design + metrics)
→ [you paste custdev results] → Reporter → CEO (go/no-go)
```

**Phase 1 — venture studio** (runs automatically for each idea the CEO marks `go`)

```
CEO go ──▶ Market Researcher → Product Manager → Tech Lead
        → Growth Marketer → Finance (CFO) → COO (execution plan) → HTML dashboard
```

If the CEO green-lights nothing (e.g. all `iterate`/`no-go`), the studio is
skipped and the run ends after the CEO — the conveyor only spends on ideas worth
building.

### The agents (roles)

| Role | Tier | What it moves forward |
|---|---|---|
| Idea Generator | worker | Diverse, testable business hypotheses |
| Research Analyst | lead | Validation design, metrics & go/no-go thresholds |
| Reporter | worker | A scannable validation report |
| CEO | lead | Go/no-go decision and prioritization |
| Market Researcher | lead | Market size, competitors, the winning wedge |
| Product Manager | lead | Sharp MVP scope, user stories, success metrics |
| Tech Lead | lead | Feasibility, stack, build estimate & risks |
| Growth Marketer | worker | Compliant GTM and the first-100-users plan |
| Finance (CFO) | lead | Pricing, unit economics, burn & runway |
| COO | lead | One fused execution plan + the owner's decisions |

Adding a worker is just a new `roles/<name>.md` + a `RoleSpec` in
`registry.py` (and a node/edge if it joins the graph) — see
[src/conveer/registry.py](src/conveer/registry.py).

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
When the venture studio runs, it also writes `execution_plan.json`,
`market_research.json`, `product_plans.json`, `tech_assessments.json`,
`gtm_plans.json`, and `finance_models.json`.
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
