"""Typed contracts shared by the conversational Agent, API and audit layer."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


PermissionLevel = Literal["read", "write", "critical", "manual"]


class AgentAction(BaseModel):
    """A verified action proposed by the chat tool agent."""

    intent: str
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    reply: str = ""
    permission_level: PermissionLevel = "read"
    confirmation_required: bool = False


class ToolCallRecord(BaseModel):
    tool_call_id: str
    run_id: str
    thread_id: str = ""
    project_id: str
    article_id: str = ""
    tool_name: str
    permission_level: PermissionLevel = "read"
    input_summary: dict[str, Any] = Field(default_factory=dict)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    error_message: str = ""
    idempotency_key: str = ""


class HandoffContext(BaseModel):
    thread_id: str
    run_id: str
    project_id: str
    article_id: str
    checkpoint_kind: str = ""
    workflow_step: str = ""
    workbench_step: int = 1
    workbench_stage: str = ""
    return_path: str
    data_version: str = ""
    created_at: str


class WorkbenchDiff(BaseModel):
    changed: bool = False
    selected_resources: dict[str, Any] = Field(default_factory=dict)
    standard_tables: dict[str, Any] = Field(default_factory=dict)
    rules: dict[str, Any] = Field(default_factory=dict)
    candidate_cells: dict[str, Any] = Field(default_factory=dict)
    reviews: dict[str, Any] = Field(default_factory=dict)
    summary: list[str] = Field(default_factory=list)


class EvidenceBundle(BaseModel):
    document_id: str
    evidence_kind: str
    article_id: str = ""
    article_title: str = ""
    sample_id: str = ""
    target_header: str = ""
    standardized_value: str = ""
    candidate_value: str = ""
    original_field: str = ""
    original_value: str = ""
    original_unit: str = ""
    target_unit: str = ""
    resource_id: str = ""
    resource_name: str = ""
    element_id: str = ""
    element_type: str = ""
    page_number: int | None = None
    bbox: list[float] = Field(default_factory=list)
    source_quote: str = ""
    mapping_rule_id: str = ""
    calculation_id: str = ""
    calculation_formula: str = ""
    review_status: str = ""
    confidence: float = 0.0
    source_complete: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class GroundedAnswer(BaseModel):
    answer: str
    citations: list[dict[str, Any]] = Field(default_factory=list)
    suggested_actions: list[str] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    actual_model: dict[str, str] = Field(default_factory=dict)
    grounded: bool = True


class LiteratureSearchResult(BaseModel):
    result_id: str
    title: str
    doi: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str = ""
    landing_url: str = ""
    pdf_url: str = ""
    open_access: bool = False
    provider: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
