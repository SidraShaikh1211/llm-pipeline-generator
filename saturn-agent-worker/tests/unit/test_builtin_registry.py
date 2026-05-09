from __future__ import annotations

from saturn_agent.tools import builtin_registry


def test_builtin_registry_has_all_tools() -> None:
    reg = builtin_registry()
    assert set(reg.names()) == {
        "convention_check",
        "find_sibling",
        "grep",
        "list_dir",
        "read_conventions",
        "read_file",
        "read_reference",
        "read_skill",
        "run_devbox_tests",
        "run_local_tests",
        "scaffold_test_fixture",
        "scalac",
        "scalafmt",
        "write_file",
    }
