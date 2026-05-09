from __future__ import annotations

from pathlib import Path

from saturn_conventions.extractors.provider import extract_provider, is_provider
from saturn_conventions.parser import parse_source


FIXTURE = Path(__file__).parent / "fixtures" / "saturn-pipelines-reports" / "foo_reports"


def _parse_first_class(src: bytes):
    tree = parse_source(src)
    for c in tree.root_node.children:
        if c.type == "class_definition":
            return c, src
    raise AssertionError("no class found")


def test_is_provider_detects_rocket_builder() -> None:
    src = (FIXTURE / "FooRocketProvider.scala").read_bytes()
    cls, source = _parse_first_class(src)
    assert is_provider(cls, source) is True


def test_is_provider_rejects_plain_class() -> None:
    cls, source = _parse_first_class(b"class FooNode extends Node[Common] {}\n")
    assert is_provider(cls, source) is False


def test_extract_provider_basics() -> None:
    src = (FIXTURE / "FooRocketProvider.scala").read_bytes()
    cls, source = _parse_first_class(src)
    conv = extract_provider(cls, source, Path("foo_reports/FooRocketProvider.scala"))
    assert conv.name == "FooRocketProvider"
    assert conv.rocket_builder_root_arg == "foo_reports"
    assert conv.finish_called is True


def test_extract_provider_global_args() -> None:
    src = (FIXTURE / "FooRocketProvider.scala").read_bytes()
    cls, source = _parse_first_class(src)
    conv = extract_provider(cls, source, Path("p.scala"))
    arg_names = [a[0] for a in conv.global_args]
    assert "lrdCode" in arg_names
    assert "reportingViewCode" in arg_names


def test_extract_provider_node_registrations() -> None:
    src = (FIXTURE / "FooRocketProvider.scala").read_bytes()
    cls, source = _parse_first_class(src)
    conv = extract_provider(cls, source, Path("p.scala"))
    assert len(conv.nodes) == 2

    foo = conv.nodes[0]
    assert foo.node_class == "FooNode"
    assert foo.node_id == "foo_workflow"
    assert foo.children == ["foo_persist"]
    pairs = dict(foo.node_args)
    assert pairs == {"reportCode": "FOO_BO", "reportDescription": "Foo Reports"}

    persist = conv.nodes[1]
    assert persist.node_class == "FooPersistNode"
    assert persist.node_id == "foo_persist"
    assert persist.children == []
    assert persist.node_args == []


def test_extract_provider_without_finish() -> None:
    src = b"""
    class StubProvider extends RocketBuilder("stub") {
      globalArg("x").add(new StubNode(_), "stub_workflow", List())
    }
    """
    cls, source = _parse_first_class(src)
    conv = extract_provider(cls, source, Path("p.scala"))
    assert conv.finish_called is False
