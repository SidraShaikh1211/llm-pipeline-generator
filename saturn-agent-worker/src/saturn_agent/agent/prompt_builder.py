from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_VERSIONS: dict[str, str] = {
    "planner": "v1",
    "generator": "v1",
    "repair": "v1",
}


_DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


@dataclass
class PromptBuilder:
    prompts_dir: Path = _DEFAULT_PROMPTS_DIR
    versions: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_VERSIONS))

    def _load(self, kind: str) -> str:
        version = self.versions.get(kind)
        if not version:
            raise KeyError(f"no prompt version configured for {kind!r}")
        path = self.prompts_dir / kind / f"{version}.md"
        if not path.is_file():
            raise FileNotFoundError(f"prompt template not found: {path}")
        return path.read_text(encoding="utf-8")

    def build_planner_prompt(
        self,
        *,
        spec: dict[str, Any],
        conventions: dict[str, Any] | str | None = None,
    ) -> str:
        template = self._load("planner")
        sections = [
            template,
            "",
            "## Spec",
            "```json",
            json.dumps(spec, indent=2, sort_keys=True),
            "```",
        ]
        if conventions is not None:
            sections += [
                "",
                "## Conventions slice",
                "```json",
                conventions if isinstance(conventions, str) else json.dumps(conventions, indent=2),
                "```",
            ]
        return "\n".join(sections)

    def build_generator_prompt(
        self,
        *,
        skill_text: str,
        references: dict[str, str] | None,
        conventions: dict[str, Any] | str | None,
        plan: dict[str, Any],
        current_file: dict[str, Any],
    ) -> str:
        template = self._load("generator")
        sections = [template, "", "## Active skill", skill_text]
        if references:
            sections += ["", "## References"]
            for name, body in references.items():
                sections += [f"### {name}", body]
        if conventions is not None:
            sections += [
                "",
                "## Conventions slice",
                "```json",
                conventions if isinstance(conventions, str) else json.dumps(conventions, indent=2),
                "```",
            ]
        sections += [
            "",
            "## Plan",
            "```json",
            json.dumps(plan, indent=2, sort_keys=True),
            "```",
            "",
            "## Current file",
            "```json",
            json.dumps(current_file, indent=2, sort_keys=True),
            "```",
        ]
        return "\n".join(sections)

    def build_repair_prompt(
        self,
        *,
        errors: str,
        affected_files: list[str],
    ) -> str:
        template = self._load("repair")
        sections = [
            template,
            "",
            "## Errors",
            "```",
            errors,
            "```",
            "",
            "## Affected files",
        ]
        sections += [f"- {f}" for f in affected_files]
        return "\n".join(sections)
