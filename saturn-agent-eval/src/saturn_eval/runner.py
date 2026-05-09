from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from saturn_eval.dataset import Case
from saturn_eval.grader import WorkerOutput


class RunnerError(Exception):
    pass


@dataclass
class RunnerOptions:
    worker_cli: Sequence[str] | None = None
    skills_root: Path | None = None
    conventions_path: Path | None = None
    timeout_s: int = 600
    extra_env: dict[str, str] | None = None


class WorkerRunner:
    """Invokes saturn-agent-worker against a case spec and collects outputs.

    By default it shells out to whatever `saturn-agent` resolves to on PATH.
    Tests may inject a fake by passing ``options.worker_cli=["python", "fake_worker.py"]``.
    """

    def __init__(self, options: RunnerOptions | None = None) -> None:
        self.options = options or RunnerOptions()

    def run(self, case: Case, output_root: Path) -> WorkerOutput:
        worker_cli = self._resolve_worker_cli()
        workspace = (output_root / case.case_id / "workspace").resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        cmd: list[str] = [
            *worker_cli,
            "generate",
            "--spec",
            str((case.case_dir / "spec.yaml").resolve()),
            "--workspace",
            str(workspace),
            "--json-output",
        ]
        mock_script = case.case_dir / "mock_responses.json"
        if mock_script.is_file():
            cmd += ["--mock-script", str(mock_script.resolve())]
        if self.options.skills_root is not None:
            cmd += ["--skills-root", str(self.options.skills_root.resolve())]
        if self.options.conventions_path is not None:
            cmd += ["--conventions", str(self.options.conventions_path.resolve())]

        env = dict(os.environ)
        if self.options.extra_env:
            env.update(self.options.extra_env)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.options.timeout_s,
                env=env,
                check=False,
            )
        except FileNotFoundError as e:
            raise RunnerError(
                f"worker CLI not found: {' '.join(cmd[:1])}. "
                f"Install saturn-agent-worker or pass --worker-cli."
            ) from e
        except subprocess.TimeoutExpired as e:
            raise RunnerError(
                f"worker timed out after {self.options.timeout_s}s for case {case.case_id}"
            ) from e

        summary = self._parse_json_summary(result.stdout)
        # Worker exit codes: 0 SUCCEEDED, 1 FAILED, 2 PAUSED_FOR_USER, 3 unexpected.
        # We grade what the worker produced regardless of exit code; the grader will
        # mark gates as failed/not-implemented as appropriate.
        if result.returncode not in (0, 1, 2):
            raise RunnerError(
                f"worker errored unexpectedly (rc={result.returncode}) for case "
                f"{case.case_id}:\n{result.stderr.strip() or result.stdout.strip()}"
            )

        generated = self._collect_generated(workspace, summary)
        return WorkerOutput(
            output_dir=workspace,
            generated_files=generated,
        )

    def _resolve_worker_cli(self) -> list[str]:
        if self.options.worker_cli:
            return list(self.options.worker_cli)
        on_path = shutil.which("saturn-agent")
        if on_path:
            return [on_path]
        # Helpful error: the worker pkg isn't installed in this venv.
        raise RunnerError(
            "saturn-agent CLI not found on PATH. Install saturn-agent-worker into "
            "this environment, or pass options.worker_cli=[...] explicitly."
        )

    @staticmethod
    def _parse_json_summary(stdout: str) -> dict | None:
        stdout = stdout.strip()
        if not stdout:
            return None
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _collect_generated(workspace: Path, summary: dict | None) -> list[str]:
        if summary and isinstance(summary.get("generated_files"), list):
            return [str(p) for p in summary["generated_files"]]
        return sorted(
            str(p.relative_to(workspace))
            for p in workspace.rglob("*")
            if p.is_file()
        )
