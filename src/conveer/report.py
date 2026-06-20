"""Render the final run into a self-contained HTML dashboard."""

from __future__ import annotations

import html
import json
from typing import Any

_CSS = """
body{font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif;max-width:860px;
margin:40px auto;padding:0 20px;color:#1b1f23}
h1{font-size:24px} h2{font-size:18px;margin-top:28px;border-bottom:1px solid #eee;padding-bottom:4px}
.meta{color:#666;font-size:13px;margin-bottom:8px}
table{border-collapse:collapse;width:100%;margin:12px 0}
th,td{border:1px solid #e1e4e8;padding:8px 10px;text-align:left;vertical-align:top}
th{background:#f6f8fa}
.tag{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
.go{background:#d6f5dd;color:#0a6b22} .nogo{background:#ffd9d9;color:#a01313}
.iterate{background:#fff3cd;color:#7a5b00}
pre.md{white-space:pre-wrap;background:#f6f8fa;border:1px solid #e1e4e8;border-radius:6px;padding:16px}
"""


def _decision_tag(decision: str) -> str:
    cls = {"go": "go", "no-go": "nogo", "iterate": "iterate"}.get(
        (decision or "").lower(), "iterate"
    )
    return f'<span class="tag {cls}">{html.escape(decision or "?")}</span>'


def render_html(state: dict[str, Any]) -> str:
    run_id = state.get("run_id", "")
    topic = state.get("topic", "")
    cost = state.get("cost", {}) or {}
    decision = state.get("decision", {}) or {}
    report_md = state.get("report_md", "")

    rows = ""
    for d in decision.get("decisions", []):
        rows += (
            "<tr>"
            f"<td>{html.escape(str(d.get('idea_id','')))}</td>"
            f"<td>{html.escape(str(d.get('title','')))}</td>"
            f"<td>{_decision_tag(str(d.get('decision','')))}</td>"
            f"<td>{html.escape(str(d.get('reason','')))}</td>"
            f"<td>{html.escape(str(d.get('next_step','')))}</td>"
            "</tr>"
        )
    decisions_table = (
        "<table><tr><th>Idea</th><th>Title</th><th>Decision</th>"
        "<th>Reason</th><th>Next step</th></tr>" + rows + "</table>"
        if rows
        else "<p><em>No decisions recorded.</em></p>"
    )

    needs_human = decision.get("needs_human", [])
    needs_human_html = (
        "<ul>" + "".join(f"<li>{html.escape(str(x))}</li>" for x in needs_human) + "</ul>"
        if needs_human
        else "<p><em>None.</em></p>"
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Conveer report — {html.escape(topic)}</title><style>{_CSS}</style></head><body>
<h1>Validation Report</h1>
<div class="meta">Run: {html.escape(run_id)} &nbsp;·&nbsp; Topic: {html.escape(topic)}
 &nbsp;·&nbsp; Cost: {cost.get('tokens',0)} tokens / ${cost.get('usd',0)}</div>
<h2>CEO summary</h2>
<p>{html.escape(str(decision.get('summary','—')))}</p>
<h2>Decisions</h2>
{decisions_table}
<h2>Needs human (owner veto/approval)</h2>
{needs_human_html}
<h2>Full report</h2>
<pre class="md">{html.escape(report_md)}</pre>
</body></html>"""


def render_decision_json(state: dict[str, Any]) -> str:
    return json.dumps(state.get("decision", {}), ensure_ascii=False, indent=2)
