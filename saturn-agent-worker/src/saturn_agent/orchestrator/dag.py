"""DAG validation and topological ordering for the planner output.

The planner emits ``Plan.files`` in whatever order the LLM happened to write them.
``FilePlan.depends_on`` is a list of other plan paths the file references; the
generator wants those upstream files written first so it can read them as
context.

This module is purely deterministic. It does not invoke the LLM or touch disk.
The single public entry point is ``validate_and_sort``: it raises ``DAGError``
on a malformed plan, otherwise returns a new ``Plan`` with ``files``
topologically ordered (Kahn's algorithm, insertion-stable on ties so the LLM's
own ordering is preserved among independent nodes).
"""

from __future__ import annotations

from collections import deque

from saturn_agent.orchestrator.plan import Plan


class DAGError(Exception):
    """Base class for DAG validation failures on a Plan."""


class CycleError(DAGError):
    """A cycle was detected in the plan's depends_on graph."""


class DanglingDepError(DAGError):
    """A FilePlan declares a depends_on path that no other FilePlan owns."""


class DuplicatePathError(DAGError):
    """Two FilePlans share the same path."""


def validate_and_sort(plan: Plan) -> Plan:
    files = plan.files
    paths_in_order = [f.path for f in files]

    seen: set[str] = set()
    for path in paths_in_order:
        if path in seen:
            raise DuplicatePathError(f"duplicate file path in plan: {path!r}")
        seen.add(path)

    by_path = {f.path: f for f in files}

    for f in files:
        for dep in f.depends_on:
            if dep not in by_path:
                raise DanglingDepError(
                    f"{f.path!r} depends_on {dep!r}, "
                    f"but no FilePlan owns that path"
                )

    indegree: dict[str, int] = {p: 0 for p in paths_in_order}
    children: dict[str, list[str]] = {p: [] for p in paths_in_order}
    for f in files:
        for dep in f.depends_on:
            indegree[f.path] += 1
            children[dep].append(f.path)

    ready: deque[str] = deque(p for p in paths_in_order if indegree[p] == 0)
    ordered: list[str] = []
    while ready:
        p = ready.popleft()
        ordered.append(p)
        for child in children[p]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if len(ordered) != len(paths_in_order):
        unresolved = [p for p in paths_in_order if indegree[p] > 0]
        raise CycleError(
            f"cycle in plan depends_on graph; unresolved files: {unresolved}"
        )

    sorted_files = [by_path[p] for p in ordered]
    return plan.model_copy(update={"files": sorted_files})
