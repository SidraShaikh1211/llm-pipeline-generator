from __future__ import annotations

import re

from tree_sitter import Node

from agent_conventions.parser import children_of_type, node_text


def class_name(class_def: Node) -> str | None:
    for child in class_def.children:
        if child.type == "identifier":
            return child.text.decode("utf-8")
    return None


def classify_naming(name: str) -> str:
    if "_" in name:
        return "snake_case"
    if re.match(r"^[a-z][a-zA-Z0-9]*$", name):
        return "camelCase"
    return "other"


def annotation_summary(ann: Node, source: bytes) -> str:
    """Render an annotation node back as roughly its source form."""
    return node_text(ann, source).strip()


def annotation_string_args(ann: Node, source: bytes) -> list[str]:
    """For an annotation like @Reference("a", "b"), returns ["a", "b"]."""
    args = next((c for c in ann.children if c.type == "arguments"), None)
    if args is None:
        return []
    out: list[str] = []
    for c in args.named_children:
        if c.type == "string":
            text = node_text(c, source)
            if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
                out.append(text[1:-1])
            else:
                out.append(text)
    return out


def annotation_name(ann: Node) -> str | None:
    for child in ann.children:
        if child.type == "type_identifier":
            return child.text.decode("utf-8")
    return None


def type_text(class_param: Node, source: bytes) -> str | None:
    """Text of the type annotation on a class_parameter (e.g., 'DataFrame', 'Dataset[Common]')."""
    saw_colon = False
    for c in class_param.children:
        if c.type == ":":
            saw_colon = True
            continue
        if saw_colon and c.type in {
            "type_identifier",
            "generic_type",
            "stable_type_identifier",
            "compound_type",
            "function_type",
            "tuple_type",
        }:
            return node_text(c, source).strip()
    return None


def param_identifier(class_param: Node) -> str | None:
    for c in class_param.children:
        if c.type == "identifier":
            return c.text.decode("utf-8")
    return None


def class_parameters(class_def: Node) -> list[Node]:
    cps = next((c for c in class_def.children if c.type == "class_parameters"), None)
    if cps is None:
        return []
    return children_of_type(cps, "class_parameter")
