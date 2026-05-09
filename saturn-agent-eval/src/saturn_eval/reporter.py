from __future__ import annotations

import json
from pathlib import Path

from saturn_eval.grader import GateStatus, SuiteScore


def write_json_report(suite: SuiteScore, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(suite.model_dump_json(indent=2))


def render_text_summary(suite: SuiteScore) -> str:
    lines = [f"Aggregate score: {suite.aggregate:.2%}", ""]
    for c in suite.cases:
        lines.append(f"  {c.case_id}: {c.passed}/{c.total} ({c.score:.2%})")
        for gate in c.gates:
            marker = {
                GateStatus.PASSED: "+",
                GateStatus.FAILED: "-",
                GateStatus.SKIPPED: "~",
                GateStatus.NOT_IMPLEMENTED: "?",
            }[gate.status]
            detail = f" — {gate.detail}" if gate.detail else ""
            lines.append(f"    {marker} {gate.name}{detail}")
    return "\n".join(lines)
