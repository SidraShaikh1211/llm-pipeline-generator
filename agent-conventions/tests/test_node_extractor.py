from __future__ import annotations

from pathlib import Path

import pytest

from agent_conventions.extractors.node import extract_node
from agent_conventions.parser import parse_source
from agent_conventions.schemas.conventions import NodeStyle


FIXTURE = Path(__file__).parent / "fixtures" / "pipelines-reports" / "foo_reports"


def _parse_first_class(src: bytes):
    tree = parse_source(src)
    for c in tree.root_node.children:
        if c.type == "class_definition":
            return c, src
    raise AssertionError("no class found")


def test_extract_style_a_node_with_reference_and_traits() -> None:
    src = (FIXTURE / "FooNode.scala").read_bytes()
    cls, source = _parse_first_class(src)
    conv = extract_node(cls, source, Path("foo_reports/FooNode.scala"))
    assert conv.class_name == "FooNode"
    assert conv.style == NodeStyle.A
    assert conv.return_type == "Node[Common]"
    assert conv.traits == ["ImpalaUtils"]
    assert conv.references == [("cdl", "cdl_d_party")]
    names = [p.name for p in conv.constructor_params]
    assert names == ["partyDf", "reportCode", "reportDescription", "lrdCode"]
    assert conv.constructor_params[0].annotation is not None
    assert "Reference" in conv.constructor_params[0].annotation
    assert all(p.naming == "camelCase" for p in conv.constructor_params)


def test_extract_style_b_node_with_dataset_param() -> None:
    src = b"""
    class BarNode(
        upstream: Dataset[Common],
        @GlobalArg("x") x: String
    ) extends Node[Common] {}
    """
    cls, source = _parse_first_class(src)
    conv = extract_node(cls, source, Path("bar/BarNode.scala"))
    assert conv.style == NodeStyle.B
    assert conv.references == []
    assert conv.constructor_params[0].type == "Dataset[Common]"


def test_extract_node_unknown_when_no_signals() -> None:
    src = b"class Plain extends Node[Common] {}\n"
    cls, source = _parse_first_class(src)
    conv = extract_node(cls, source, Path("plain.scala"))
    assert conv.style == NodeStyle.UNKNOWN
    assert conv.constructor_params == []


def test_naming_classification_snake_case() -> None:
    src = b"""
    class Snake(
        snake_param: String
    ) extends Node[Common] {}
    """
    cls, source = _parse_first_class(src)
    conv = extract_node(cls, source, Path("s.scala"))
    assert conv.constructor_params[0].naming == "snake_case"


def test_extract_collects_multiple_traits() -> None:
    src = b"""
    class Multi(
        @Reference("a", "b") df: DataFrame
    ) extends Node[Common] with ImpalaUtils with TraitTwo {}
    """
    cls, source = _parse_first_class(src)
    conv = extract_node(cls, source, Path("m.scala"))
    assert conv.traits == ["ImpalaUtils", "TraitTwo"]
