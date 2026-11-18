from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from agent_eval.dataset import Case


class GateStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_IMPLEMENTED = "not_implemented"


class GateResult(BaseModel):
    name: str
    status: GateStatus
    detail: str | None = None


class CaseScore(BaseModel):
    case_id: str
    gates: list[GateResult]

    @property
    def total(self) -> int:
        return sum(1 for g in self.gates if g.status != GateStatus.SKIPPED)

    @property
    def passed(self) -> int:
        return sum(1 for g in self.gates if g.status == GateStatus.PASSED)

    @property
    def score(self) -> float:
        if self.total == 0:
            return 0.0
        return self.passed / self.total


class SuiteScore(BaseModel):
    cases: list[CaseScore] = Field(default_factory=list)

    @property
    def aggregate(self) -> float:
        if not self.cases:
            return 0.0
        return sum(c.score for c in self.cases) / len(self.cases)


class WorkerOutput(BaseModel):
    """What the runner gives the grader after invoking the worker."""

    output_dir: Path
    generated_files: list[str]
    compile_ok: bool | None = None
    convention_violations: list[str] | None = None
    test_pass_rate: float | None = None

    model_config = {"arbitrary_types_allowed": True}


class Grader:
    def grade(self, case: Case, output: WorkerOutput) -> CaseScore:
        gates: list[GateResult] = []
        g = case.grading

        if g.must_compile:
            gates.append(self._gate_compile(output))
        if g.must_pass_convention_checks:
            gates.append(self._gate_conventions(output))
        if g.files_must_exist:
            gates.append(self._gate_files_exist(g.files_must_exist, output))
        if g.test_pass_rate_minimum is not None:
            gates.append(self._gate_test_pass_rate(g.test_pass_rate_minimum, output))
        if g.provider is not None:
            gates.append(self._gate_provider())
        if g.ddl is not None and g.ddl.expected:
            gates.append(self._gate_ddl())

        return CaseScore(case_id=case.case_id, gates=gates)

    @staticmethod
    def _gate_compile(output: WorkerOutput) -> GateResult:
        if output.compile_ok is None:
            return GateResult(name="must_compile", status=GateStatus.NOT_IMPLEMENTED)
        if output.compile_ok:
            return GateResult(name="must_compile", status=GateStatus.PASSED)
        return GateResult(name="must_compile", status=GateStatus.FAILED, detail="compile failed")

    @staticmethod
    def _gate_conventions(output: WorkerOutput) -> GateResult:
        if output.convention_violations is None:
            return GateResult(name="must_pass_convention_checks", status=GateStatus.NOT_IMPLEMENTED)
        if not output.convention_violations:
            return GateResult(name="must_pass_convention_checks", status=GateStatus.PASSED)
        return GateResult(
            name="must_pass_convention_checks",
            status=GateStatus.FAILED,
            detail=f"{len(output.convention_violations)} violations",
        )

    @staticmethod
    def _gate_files_exist(expected: list[str], output: WorkerOutput) -> GateResult:
        missing = [f for f in expected if f not in output.generated_files]
        if not missing:
            return GateResult(name="files_must_exist", status=GateStatus.PASSED)
        return GateResult(
            name="files_must_exist",
            status=GateStatus.FAILED,
            detail=f"missing: {', '.join(missing)}",
        )

    @staticmethod
    def _gate_test_pass_rate(minimum: float, output: WorkerOutput) -> GateResult:
        if output.test_pass_rate is None:
            return GateResult(name="test_pass_rate_minimum", status=GateStatus.NOT_IMPLEMENTED)
        if output.test_pass_rate >= minimum:
            return GateResult(name="test_pass_rate_minimum", status=GateStatus.PASSED)
        return GateResult(
            name="test_pass_rate_minimum",
            status=GateStatus.FAILED,
            detail=f"{output.test_pass_rate:.2f} < {minimum:.2f}",
        )

    @staticmethod
    def _gate_provider() -> GateResult:
        # Requires AST-level parsing of generated Provider — wired up once the
        # worker emits structured plan output the grader can consume.
        return GateResult(name="provider", status=GateStatus.NOT_IMPLEMENTED)

    @staticmethod
    def _gate_ddl() -> GateResult:
        return GateResult(name="ddl", status=GateStatus.NOT_IMPLEMENTED)
