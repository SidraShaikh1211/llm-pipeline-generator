from __future__ import annotations

import json
from pathlib import Path

import pytest

from saturn_agent.agent.client import load_fake_client_from_script


def test_loader_parses_text_only(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"responses": [{"text": "hello", "tokens_used": 7}]}))
    client = load_fake_client_from_script(p)
    result = client.generate(system_prompt="", messages=[], tools=None)
    assert result.text == "hello"
    assert result.tokens_used == 7
    assert result.tool_calls == []


def test_loader_parses_tool_calls(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(
        json.dumps(
            {
                "responses": [
                    {
                        "tool_calls": [
                            {
                                "id": "c1",
                                "name": "write_file",
                                "arguments": {"path": "x.txt", "content": "y"},
                            }
                        ],
                        "tokens_used": 12,
                    }
                ]
            }
        )
    )
    client = load_fake_client_from_script(p)
    result = client.generate(system_prompt="", messages=[], tools=None)
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "write_file"
    assert result.tool_calls[0].arguments["path"] == "x.txt"


def test_loader_default_id(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(
        json.dumps(
            {"responses": [{"tool_calls": [{"name": "read_file", "arguments": {}}]}]}
        )
    )
    client = load_fake_client_from_script(p)
    result = client.generate(system_prompt="", messages=[], tools=None)
    assert result.tool_calls[0].id  # auto-assigned


def test_loader_rejects_malformed_root(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text("[]")
    with pytest.raises(ValueError, match="responses"):
        load_fake_client_from_script(p)


def test_loader_rejects_non_dict_response(tmp_path: Path) -> None:
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"responses": ["bad"]}))
    with pytest.raises(ValueError, match="response 0"):
        load_fake_client_from_script(p)
