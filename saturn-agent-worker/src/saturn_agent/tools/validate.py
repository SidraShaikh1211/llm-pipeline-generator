from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel

from saturn_agent.tools.base import Tool, ToolContext, ToolError, ToolSpec


SCALAFMT_TIMEOUT_S = 60
SCALAC_TIMEOUT_S = 180
SBT_COMPILE_TIMEOUT_S = 600


def _scalafmt_cwd(ctx: ToolContext) -> Path | None:
    """Return a directory whose .scalafmt.conf scalafmt should pick up, else None.

    scalafmt walks up from cwd looking for its config file. We prefer the
    cloned target repo's config (so generated code matches that repo's
    formatting rules) and fall back to a workspace-level .scalafmt.conf if
    one's been planted there — useful for jobs that run without a clone and
    still want a reproducible format step.
    """
    repo_dir = ctx.target_repo_path
    if repo_dir and repo_dir.is_dir() and (repo_dir / ".scalafmt.conf").is_file():
        return repo_dir
    workspace = ctx.workspace_path
    if workspace and workspace.is_dir() and (workspace / ".scalafmt.conf").is_file():
        return workspace
    return None


def _sbt_compile_cwd(ctx: ToolContext) -> Path | None:
    """Return the cloned target repo dir if it's a real sbt project, else None.

    `sbt compile` resolves the full project classpath (Spark, framework
    stubs, scalatest, etc.) — something raw `scalac <file>` can never do
    without a hand-built classpath. If the clone isn't an sbt project we
    fall back to plain scalac for self-contained sources.
    """
    repo_dir = ctx.target_repo_path
    if repo_dir and repo_dir.is_dir() and (repo_dir / "build.sbt").is_file():
        return repo_dir
    return None


# ---------- scalafmt / scalac ----------


def _resolve_paths(paths: list[Any], ctx: ToolContext) -> list[str]:
    out: list[str] = []
    for p in paths:
        if not isinstance(p, str) or not p:
            raise ToolError("each path must be a non-empty string")
        target = ctx.resolve_within_workspace(p)
        if not target.is_file():
            raise ToolError(f"path is not a file: {p}")
        out.append(str(target))
    return out


class ScalafmtTool(Tool):
    BINARY: ClassVar[str] = "scalafmt"

    spec = ToolSpec(
        name="scalafmt",
        description=(
            "Run scalafmt over the listed files (formats in place). "
            "If scalafmt is not installed, returns a 'skipping' message instead of failing."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["paths"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        paths = arguments.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ToolError("scalafmt requires a non-empty 'paths' list")

        binary = shutil.which(self.BINARY)
        if binary is None:
            return f"{self.BINARY} not installed; skipping ({len(paths)} files)"

        resolved = _resolve_paths(paths, ctx)
        cwd = _scalafmt_cwd(ctx)
        try:
            result = subprocess.run(
                [binary, *resolved],
                capture_output=True,
                text=True,
                timeout=SCALAFMT_TIMEOUT_S,
                check=False,
                cwd=str(cwd) if cwd else None,
            )
        except subprocess.TimeoutExpired:
            return f"scalafmt timed out after {SCALAFMT_TIMEOUT_S}s"

        if result.returncode != 0:
            return (
                f"scalafmt failed (rc={result.returncode}):\n"
                f"{result.stderr.strip()}\n{result.stdout.strip()}"
            ).strip()
        return f"scalafmt ok ({len(paths)} files)"


class ScalacTool(Tool):
    BINARY: ClassVar[str] = "scalac"

    spec = ToolSpec(
        name="scalac",
        description=(
            "Compile Scala files with scalac, returning compiler output. "
            "If scalac is not installed, returns a 'skipping' message instead of failing."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
                "classpath": {
                    "type": "string",
                    "description": "Classpath passed to scalac. May be empty for self-contained sources.",
                    "default": "",
                },
                "out_dir": {
                    "type": "string",
                    "description": "Workspace-relative output directory for class files.",
                    "default": "outputs/scalac",
                },
            },
            "required": ["paths"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        paths = arguments.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ToolError("scalac requires a non-empty 'paths' list")

        # Validate paths exist now so failure messages are uniform regardless
        # of whether we end up running sbt or scalac below.
        _resolve_paths(paths, ctx)

        sbt_dir = _sbt_compile_cwd(ctx)
        if sbt_dir is not None and shutil.which("sbt") is not None:
            return self._run_sbt_compile(sbt_dir, len(paths))

        binary = shutil.which(self.BINARY)
        if binary is None:
            return f"{self.BINARY} not installed; skipping ({len(paths)} files)"

        out_dir = arguments.get("out_dir") or "outputs/scalac"
        out_path = ctx.resolve_within_workspace(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        resolved = _resolve_paths(paths, ctx)
        cmd = [binary, "-d", str(out_path)]
        classpath = arguments.get("classpath") or ""
        if classpath:
            cmd += ["-classpath", classpath]
        cmd += resolved

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SCALAC_TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"scalac timed out after {SCALAC_TIMEOUT_S}s"

        combined = (result.stderr + result.stdout).strip()
        if result.returncode != 0:
            return f"scalac failed (rc={result.returncode}):\n{combined}"
        return f"scalac ok ({len(paths)} files){chr(10) + combined if combined else ''}"

    @staticmethod
    def _run_sbt_compile(repo_dir: Path, n_paths: int) -> str:
        """Compile the entire sbt project and surface compiler output.

        The 'scalac' wrapping is intentional: stage_validating keys off
        '.startswith("scalac failed")' / 'scalac ok', so the return string
        keeps that prefix even when sbt is the underlying compiler.
        """
        cmd = [
            "sbt",
            "-batch",
            "-no-colors",
            "-Dsbt.log.noformat=true",
            "compile",
        ]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=SBT_COMPILE_TIMEOUT_S,
                check=False,
                cwd=str(repo_dir),
            )
        except subprocess.TimeoutExpired:
            return f"scalac failed: sbt compile timed out after {SBT_COMPILE_TIMEOUT_S}s"

        combined = (result.stdout + result.stderr).strip()
        # sbt logs are verbose; the agent only needs the [error] lines and the
        # immediately surrounding context. Keep the full output if it's modest.
        trimmed = _trim_sbt_output(combined)
        if result.returncode != 0:
            return f"scalac failed (sbt compile, rc={result.returncode}):\n{trimmed}"
        return f"scalac ok (sbt compile; {n_paths} new files compiled with full project classpath)"


def _trim_sbt_output(text: str, max_chars: int = 8000) -> str:
    """Keep [error] / [warn] lines plus a few neighbours; drop resolution chatter."""
    if len(text) <= max_chars:
        return text
    lines = text.splitlines()
    keep: list[str] = []
    for i, line in enumerate(lines):
        if "[error]" in line or "[warn]" in line:
            # include the line itself and the two lines after for context
            for j in range(i, min(len(lines), i + 3)):
                if lines[j] not in keep:
                    keep.append(lines[j])
    if not keep:
        return text[-max_chars:]
    out = "\n".join(keep)
    return out if len(out) <= max_chars else out[-max_chars:]


# ---------- convention_check ----------


class ConventionViolation(BaseModel):
    rule: str
    file: str
    line: int | None = None
    message: str

    def render(self) -> str:
        loc = f":{self.line}" if self.line is not None else ""
        return f"{self.file}{loc}: [{self.rule}] {self.message}"


@dataclass(frozen=True)
class _Rule:
    name: str
    applies_to_suffix: str
    check: Callable[[str, str], list[ConventionViolation]]


def _line_regex_rule(
    rule_name: str,
    suffix: str,
    pattern: str,
    message: str,
    flags: int = 0,
) -> _Rule:
    rgx = re.compile(pattern, flags)

    def fn(rel_path: str, content: str) -> list[ConventionViolation]:
        violations: list[ConventionViolation] = []
        for lineno, line in enumerate(content.splitlines(), 1):
            if rgx.search(line):
                violations.append(
                    ConventionViolation(
                        rule=rule_name, file=rel_path, line=lineno, message=message
                    )
                )
        return violations

    return _Rule(name=rule_name, applies_to_suffix=suffix, check=fn)


def _provider_must_call_finish(rel_path: str, content: str) -> list[ConventionViolation]:
    if not rel_path.endswith("RocketProvider.scala"):
        return []
    if re.search(r"\.finish\s*\(\s*\)", content):
        return []
    return [
        ConventionViolation(
            rule="provider_must_call_finish",
            file=rel_path,
            line=None,
            message="RocketProvider chain must end with .finish().",
        )
    ]


def _ddl_must_use_create_table_if_not_exists(
    rel_path: str, content: str
) -> list[ConventionViolation]:
    if not rel_path.endswith(".sql"):
        return []
    if re.search(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS", content, re.IGNORECASE):
        return []
    if not re.search(r"CREATE\s+TABLE", content, re.IGNORECASE):
        return []  # not a DDL we care about (e.g., a SELECT-only file)
    return [
        ConventionViolation(
            rule="ddl_must_use_create_table_if_not_exists",
            file=rel_path,
            line=None,
            message="DDL must use CREATE TABLE IF NOT EXISTS for idempotency.",
        )
    ]


_RULES: tuple[_Rule, ...] = (
    _line_regex_rule(
        "no_println",
        ".scala",
        r"\bprintln\s*\(",
        "println is not allowed; use a logger.",
    ),
    _line_regex_rule(
        "no_save_as_table",
        ".scala",
        r"\.saveAsTable\b",
        ".saveAsTable bypasses Saturn's persist conventions; use a Persist node.",
    ),
    _line_regex_rule(
        "no_simple_date_format",
        ".scala",
        r"\bSimpleDateFormat\b",
        "SimpleDateFormat is not thread-safe; use java.time.format.DateTimeFormatter.",
    ),
    _Rule(
        name="provider_must_call_finish",
        applies_to_suffix=".scala",
        check=_provider_must_call_finish,
    ),
    _Rule(
        name="ddl_must_use_create_table_if_not_exists",
        applies_to_suffix=".sql",
        check=_ddl_must_use_create_table_if_not_exists,
    ),
)


class ConventionCheckTool(Tool):
    spec = ToolSpec(
        name="convention_check",
        description=(
            "Run static Saturn convention checks against the listed files. "
            "Returns 'ok' when clean, otherwise a list of violations the agent should fix."
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "paths": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["paths"],
        },
    )

    def invoke(self, arguments: dict[str, Any], ctx: ToolContext) -> str:
        paths = arguments.get("paths") or []
        if not isinstance(paths, list) or not paths:
            raise ToolError("convention_check requires a non-empty 'paths' list")

        violations: list[ConventionViolation] = []
        for raw in paths:
            if not isinstance(raw, str) or not raw:
                raise ToolError("each path must be a non-empty string")
            target = ctx.resolve_within_workspace(raw)
            if not target.is_file():
                raise ToolError(f"path is not a file: {raw}")
            content = target.read_text(encoding="utf-8", errors="replace")
            for rule in _RULES:
                if not raw.endswith(rule.applies_to_suffix):
                    continue
                violations.extend(rule.check(raw, content))

        if not violations:
            return f"convention_check ok ({len(paths)} files, {len(_RULES)} rules)"
        return "\n".join(v.render() for v in violations)


def list_rules() -> list[str]:
    return [r.name for r in _RULES]
