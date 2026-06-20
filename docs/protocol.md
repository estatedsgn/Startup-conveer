# Interaction Protocol — How agents work together

Agents never free-chat. Every handoff is a typed envelope (see
`src/conveer/envelopes.py`), logged for observability.

## Task Envelope (sent to a worker)

| field | meaning |
|---|---|
| `task_id` | unique id |
| `from` / `to` | sender and recipient role |
| `objective` | what to do, one sentence |
| `inputs` | data / references |
| `constraints` | rules (e.g. "outreach is human-in-the-loop only") |
| `success_criteria` | measurable criteria the result is judged by |
| `deadline` | ISO-8601 or null |
| `priority` | P0 / P1 / P2 |
| `context_refs` | ids of artifacts/memory to load |

## Result Envelope (returned by a worker)

| field | meaning |
|---|---|
| `task_id` | matches the task |
| `status` | done / blocked / needs_human |
| `summary` | short read for the manager |
| `artifacts` | what was produced |
| `metrics` | numbers vs. success_criteria |
| `confidence` | 0.0–1.0 |
| `blockers` | what's in the way |
| `recommendation` | go / no-go / iterate |
| `cost` | tokens + USD |

## Decision rights (RACI)

- **Owner (human):** strategy, **veto**, approval of irreversible/external
  actions, sends outreach messages.
- **CEO agent:** prioritization, final go/no-go, routing between departments.
- **Department lead:** methodology, task distribution within the department.
- **Worker:** execution within its tool scope; never exceeds it.

## Escalation to human

Escalate when: an action is irreversible/external; `confidence` is below the
role's threshold; a request conflicts with the charter/compliance; or required
data is missing.

## Reporting cadence

Per task: a Result Envelope. Per day: a digest to the owner.
