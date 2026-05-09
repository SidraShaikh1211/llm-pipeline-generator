from __future__ import annotations

from pathlib import Path

from tree_sitter import Node

from saturn_conventions.extractors._common import (
    annotation_name,
    annotation_string_args,
    annotation_summary,
    class_name,
    class_parameters,
    classify_naming,
    param_identifier,
    type_text,
)
from saturn_conventions.parser import node_text
from saturn_conventions.schemas.conventions import (
    NodeConvention,
    NodeStyle,
    ParamConvention,
)


def extract_node(class_def: Node, source: bytes, file_path: Path) -> NodeConvention:
    name = class_name(class_def) or "<anonymous>"
    params, refs = _extract_params(class_def, source)
    return_type = _extract_return_type(class_def, source)
    traits = _extract_traits(class_def, source)
    style = _classify_style(params, refs, traits)

    return NodeConvention(
        class_name=name,
        file_path=str(file_path),
        style=style,
        constructor_params=params,
        return_type=return_type,
        traits=traits,
        references=refs,
    )


def _extract_params(
    class_def: Node, source: bytes
) -> tuple[list[ParamConvention], list[tuple[str, str]]]:
    params: list[ParamConvention] = []
    refs: list[tuple[str, str]] = []
    for cp in class_parameters(class_def):
        ident = param_identifier(cp)
        if ident is None:
            continue
        type_str = type_text(cp, source) or "?"
        annotation: str | None = None
        for child in cp.children:
            if child.type == "annotation":
                annotation = annotation_summary(child, source)
                if annotation_name(child) == "Reference":
                    args = annotation_string_args(child, source)
                    if len(args) >= 2:
                        refs.append((args[0], args[1]))
                break
        params.append(
            ParamConvention(
                name=ident,
                type=type_str,
                naming=classify_naming(ident),
                annotation=annotation,
            )
        )
    return params, refs


def _extends_children(class_def: Node) -> list[Node]:
    ec = next((c for c in class_def.children if c.type == "extends_clause"), None)
    if ec is None:
        return []
    return list(ec.children)


def _extract_return_type(class_def: Node, source: bytes) -> str | None:
    for c in _extends_children(class_def):
        if c.type in {"generic_type", "type_identifier", "stable_type_identifier"}:
            return node_text(c, source).strip()
    return None


def _extract_traits(class_def: Node, source: bytes) -> list[str]:
    traits: list[str] = []
    saw_with = False
    for c in _extends_children(class_def):
        if c.type == "with":
            saw_with = True
            continue
        if saw_with and c.type in {"type_identifier", "stable_type_identifier"}:
            traits.append(node_text(c, source).strip())
            saw_with = False
    return traits


def _classify_style(
    params: list[ParamConvention],
    refs: list[tuple[str, str]],
    traits: list[str],
) -> NodeStyle:
    has_reference = bool(refs)
    has_dataset = any(p.type.startswith("Dataset[") for p in params)
    if has_reference and not has_dataset:
        return NodeStyle.A
    if has_dataset and not has_reference:
        return NodeStyle.B
    if has_reference and has_dataset:
        # mixed: classify based on dominant param kind
        return NodeStyle.A if len(refs) >= 1 and not has_dataset else NodeStyle.B
    return NodeStyle.UNKNOWN
