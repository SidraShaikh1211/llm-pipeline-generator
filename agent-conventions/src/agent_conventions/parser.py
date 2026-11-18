from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

import tree_sitter_scala
from tree_sitter import Language, Node, Parser, Tree


@lru_cache(maxsize=1)
def scala_language() -> Language:
    return Language(tree_sitter_scala.language())


@lru_cache(maxsize=1)
def scala_parser() -> Parser:
    return Parser(scala_language())


def parse_source(source: str | bytes) -> Tree:
    if isinstance(source, str):
        source = source.encode("utf-8")
    return scala_parser().parse(source)


def parse_file(path: Path) -> Tree:
    return parse_source(path.read_bytes())


def node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def walk(node: Node) -> Iterable[Node]:
    """Pre-order traversal yielding every descendant of `node` including itself."""
    cursor = node.walk()
    visited_children = False
    while True:
        if not visited_children:
            yield cursor.node  # type: ignore[misc]
            if cursor.goto_first_child():
                continue
            visited_children = True
        if cursor.goto_next_sibling():
            visited_children = False
            continue
        if not cursor.goto_parent():
            return
        if cursor.node == node:  # type: ignore[union-attr]
            return


def find_first(node: Node, kind: str) -> Node | None:
    for child in walk(node):
        if child is node:
            continue
        if child.type == kind:
            return child
    return None


def find_all(node: Node, kind: str) -> list[Node]:
    return [child for child in walk(node) if child is not node and child.type == kind]


def children_of_type(node: Node, kind: str) -> list[Node]:
    return [c for c in node.named_children if c.type == kind]
