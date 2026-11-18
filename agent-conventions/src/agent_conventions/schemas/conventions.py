from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class NodeStyle(str, Enum):
    A = "A"  # single-node: constructor takes raw @Reference DataFrames
    B = "B"  # multi-node: constructor takes typed upstream Dataset[Common]
    C = "C"  # source: declares @Reference on its return type
    UNKNOWN = "unknown"


class ParamConvention(BaseModel):
    name: str
    type: str
    naming: str  # "camelCase" | "snake_case" | "other"
    annotation: str | None = None  # e.g., '@Reference("cdl", "cdl_d_party")'


class NodeRegistration(BaseModel):
    node_class: str
    node_id: str
    children: list[str] = Field(default_factory=list)
    node_args: list[tuple[str, str]] = Field(default_factory=list)


class ProviderConvention(BaseModel):
    name: str
    file_path: str
    rocket_builder_root_arg: str | None = None
    global_args: list[tuple[str, str]] = Field(default_factory=list)
    nodes: list[NodeRegistration] = Field(default_factory=list)
    finish_called: bool = False


class NodeConvention(BaseModel):
    class_name: str
    file_path: str
    style: NodeStyle = NodeStyle.UNKNOWN
    constructor_params: list[ParamConvention] = Field(default_factory=list)
    return_type: str | None = None
    traits: list[str] = Field(default_factory=list)
    references: list[tuple[str, str]] = Field(default_factory=list)


class CaseClassConvention(BaseModel):
    class_name: str
    fields: list[tuple[str, str]] = Field(default_factory=list)


class DomainConvention(BaseModel):
    common_class_fields: list[tuple[str, str]] = Field(default_factory=list)
    other_case_classes: list[CaseClassConvention] = Field(default_factory=list)


class DDLConvention(BaseModel):
    file_path: str
    table_name: str
    columns: list[tuple[str, str]] = Field(default_factory=list)
    partition_cols: list[str] = Field(default_factory=list)
    format: str | None = None


class PipelineConvention(BaseModel):
    pipeline_name: str
    folder_path: str
    provider: ProviderConvention | None = None
    nodes: list[NodeConvention] = Field(default_factory=list)
    domain: DomainConvention | None = None


class RepoConventions(BaseModel):
    repo_name: str
    extracted_at: datetime
    pipelines: list[PipelineConvention] = Field(default_factory=list)
    common_imports: list[str] = Field(default_factory=list)
