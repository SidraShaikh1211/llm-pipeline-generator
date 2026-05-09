from __future__ import annotations

from saturn_conventions.parser import parse_source


def test_parses_simple_class_without_errors() -> None:
    src = b"class Foo {}\n"
    tree = parse_source(src)
    assert not tree.root_node.has_error
    kinds = [c.type for c in tree.root_node.children]
    assert "class_definition" in kinds


def test_parses_complex_node_without_errors() -> None:
    src = b"""
    class FooNode(
        @Reference("saturn", "cdl_d_party") df: DataFrame
    ) extends Node[Common] with ImpalaUtils {
      def run(): Dataset[Common] = ???
    }
    """
    tree = parse_source(src)
    assert not tree.root_node.has_error
