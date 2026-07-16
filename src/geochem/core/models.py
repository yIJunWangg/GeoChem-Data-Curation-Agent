"""Core Pydantic data models for GeoChem."""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --- Enums ---

class ArticleStatus(str, enum.Enum):
    IMPORTED = "imported"
    PARSING = "parsing"
    PARSED = "parsed"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    MAPPING = "mapping"
    MAPPED = "mapped"
    REVIEWING = "reviewing"
    REVIEWED = "reviewed"
    EXPORTING = "exporting"
    EXPORTED = "exported"
    ERROR = "error"


class ResourceType(str, enum.Enum):
    MAIN_PDF = "main_pdf"
    SUPPLEMENTARY_EXCEL = "supplementary_excel"
    SUPPLEMENTARY_PDF = "supplementary_pdf"
    SUPPLEMENTARY_CSV = "supplementary_csv"
    SUPPLEMENTARY_DOCX = "supplementary_docx"
    SUPPLEMENTARY_ZIP = "supplementary_zip"
    HTML_PAGE = "html_page"
    FIGURE = "figure"
    DATA_LINK = "data_link"
    OTHER = "other"


class RiskLevel(str, enum.Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ReviewStatus(str, enum.Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SKIPPED = "skipped"
    DEFERRED = "deferred"


class DataQualityGrade(str, enum.Enum):
    A = "A"  # Direct match, no conversion needed
    B = "B"  # Alias mapping, confirmed
    C = "C"  # Unit/formula conversion, confirmed and archived
    D = "D"  # AI suggested, not confirmed
    E = "E"  # Ambiguous or source unclear


class ChemicalForm(str, enum.Enum):
    ELEMENT = "element"
    OXIDE = "oxide"
    INDEX = "index"
    ISOTOPE = "isotope"
    METADATA = "metadata"


class MappingType(str, enum.Enum):
    EXACT = "exact"
    ALIAS_NORMALIZATION = "alias_normalization"
    ALIAS_MAPPING = "alias_mapping"
    UNIT_CONVERSION = "unit_conversion"
    OXIDE_TO_ELEMENT = "oxide_to_element"
    ELEMENT_TO_OXIDE = "element_to_oxide"
    NEW_FIELD = "new_field"
    UNCERTAIN = "uncertain"


class ExportFormat(str, enum.Enum):
    EXCEL = "excel"
    CSV = "csv"
    SQLITE = "sqlite"
    JSONL = "jsonl"


# --- Project ---

class ProjectConfig(BaseModel):
    """Project-level configuration."""
    project_name: str
    project_id: str
    created_at: datetime = Field(default_factory=datetime.now)
    description: str = ""
    research_field: str = ""
    schema_file: str = "schema/geochem_schema.yaml"
    mapping_memory: str = "memory/mapping_rules.yaml"
    output_database: str = "output/standardized_data.sqlite"


# --- Schema ---

class SchemaField(BaseModel):
    """Definition of a single field in the schema."""
    name: str
    type: str = "string"
    description: str = ""
    default_unit: str | None = None
    aliases: list[str] = Field(default_factory=list)
    chemical_form: ChemicalForm = ChemicalForm.METADATA
    allow_conversion: bool = False
    review_required: bool = False
    required: bool = False
    is_primary_key: bool = False
    missing_value_policy: str = "skip"
    quality_grade_requirement: DataQualityGrade = DataQualityGrade.C


class GeoChemSchema(BaseModel):
    """Full schema definition."""
    schema_name: str = "geochem"
    version: str = "1.0"
    columns: list[SchemaField] = Field(default_factory=list)

    def get_field(self, name: str) -> SchemaField | None:
        for col in self.columns:
            if col.name == name:
                return col
        return None

    def get_field_names(self) -> list[str]:
        return [col.name for col in self.columns]


# --- Article & Resource ---

class Article(BaseModel):
    """Metadata for an imported article."""
    article_id: str
    title: str = ""
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    doi: str | None = None
    url: str | None = None
    journal: str = ""
    status: ArticleStatus = ArticleStatus.IMPORTED
    created_at: datetime = Field(default_factory=datetime.now)


class Resource(BaseModel):
    """A file or link associated with an article."""
    resource_id: str
    article_id: str
    resource_type: ResourceType
    file_name: str
    local_path: str | None = None
    source_url: str | None = None
    file_hash: str | None = None
    file_size: int | None = None
    relevance_score: float | None = None
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.now)


# --- Extraction ---

class CandidateColumn(BaseModel):
    """A column detected from a source table."""
    column_id: str
    table_id: str
    raw_name: str
    normalized_name: str = ""
    unit_candidate: str | None = None
    sample_values: list[Any] = Field(default_factory=list)
    dtype: str = "unknown"


class CandidateTable(BaseModel):
    """A table extracted from a resource."""
    table_id: str
    article_id: str
    resource_id: str
    source_type: str = ""
    sheet_name: str | None = None
    page_number: int | None = None
    title: str = ""
    caption: str = ""
    columns: list[CandidateColumn] = Field(default_factory=list)
    row_count: int = 0
    unit_candidates: dict[str, str] = Field(default_factory=dict)
    sample_id_candidates: list[str] = Field(default_factory=list)
    extract_method: str = ""
    confidence: float = 0.0
    created_at: datetime = Field(default_factory=datetime.now)


# --- Mapping ---

class FieldMappingSuggestion(BaseModel):
    """A suggested mapping from source field to target schema field."""
    mapping_id: str
    article_id: str
    table_id: str
    source_field: str
    target_field: str
    source_unit: str | None = None
    target_unit: str | None = None
    mapping_type: MappingType = MappingType.UNCERTAIN
    confidence: float = 0.0
    risk_level: RiskLevel = RiskLevel.MEDIUM
    requires_review: bool = True
    reason: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class MappingRule(BaseModel):
    """A confirmed mapping rule stored in memory."""
    rule_id: str
    source_field: str
    target_field: str
    source_unit: str | None = None
    target_unit: str | None = None
    mapping_type: MappingType = MappingType.EXACT
    formula: str | None = None
    conversion_factor: float | None = None
    review_status: ReviewStatus = ReviewStatus.CONFIRMED
    scope: str = "project"
    version: int = 1
    created_at: datetime = Field(default_factory=datetime.now)
    created_by: str = "user"


# --- Review ---

class ReviewItem(BaseModel):
    """An item requiring human review."""
    review_id: str
    article_id: str
    table_id: str | None = None
    row_id: str | None = None
    item_type: str = "field_mapping"
    risk_level: RiskLevel = RiskLevel.MEDIUM
    original_field: str = ""
    original_unit: str | None = None
    original_value: Any = None
    ai_suggestion: str = ""
    confidence: float = 0.0
    available_actions: list[str] = Field(default_factory=list)
    status: ReviewStatus = ReviewStatus.PENDING
    created_at: datetime = Field(default_factory=datetime.now)


class ReviewDecision(BaseModel):
    """A user's decision on a review item."""
    decision_id: str
    review_id: str
    action: str = ""
    target_field: str | None = None
    target_unit: str | None = None
    formula: str | None = None
    save_as_rule: bool = False
    rule_scope: str = "project"
    notes: str = ""
    decided_at: datetime = Field(default_factory=datetime.now)


# --- Calculation ---

class CalculationRecord(BaseModel):
    """A record of a unit/formula conversion calculation."""
    calc_id: str
    article_id: str
    table_id: str
    row_id: str
    target_field: str
    source_field: str
    source_value: float
    source_unit: str
    target_unit: str
    formula: str
    formula_source: str = ""
    substitution: str = ""
    result: float
    significant_figures: int | None = None
    rule_id: str | None = None
    review_status: ReviewStatus = ReviewStatus.CONFIRMED
    archive_file: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)


# --- Export ---

class StandardizedRecord(BaseModel):
    """A single standardized data record for export."""
    record_id: str
    article_id: str
    table_id: str
    row_id: str
    data: dict[str, Any] = Field(default_factory=dict)
    reference: str = ""
    doi: str | None = None
    source_file: str = ""
    source_table: str = ""
    source_row: int | None = None
    original_fields: dict[str, str] = Field(default_factory=dict)
    original_units: dict[str, str] = Field(default_factory=dict)
    original_values: dict[str, Any] = Field(default_factory=dict)
    mapped_fields: dict[str, str] = Field(default_factory=dict)
    mapped_units: dict[str, str] = Field(default_factory=dict)
    mapping_rule_ids: dict[str, str] = Field(default_factory=dict)
    calculation_ids: dict[str, str] = Field(default_factory=dict)
    review_statuses: dict[str, ReviewStatus] = Field(default_factory=dict)
    confidence_scores: dict[str, float] = Field(default_factory=dict)
    quality_grade: DataQualityGrade = DataQualityGrade.D
    processed_at: datetime = Field(default_factory=datetime.now)


class ExportJob(BaseModel):
    """Record of an export operation."""
    job_id: str
    project_id: str
    export_format: ExportFormat
    output_path: str
    record_count: int = 0
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.now)


# --- LLM ---

class LLMCallRecord(BaseModel):
    """Record of a single LLM API call."""
    call_id: str
    project_id: str = ""
    article_id: str = ""
    agent_name: str = ""
    skill_name: str = ""
    model_provider: str = ""
    model_name: str = ""
    prompt_version: str = ""
    prompt_hash: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int | None = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0
    started_at: datetime = Field(default_factory=datetime.now)
    ended_at: datetime | None = None
    latency_ms: int | None = None
    status: str = "success"
    error_message: str | None = None
    retry_count: int = 0
    request_summary: str = ""
    response_summary: str = ""
    reasoning_present: bool = False
    finish_reason: str = ""
    config_version: str = ""
    structured_status: str = ""


class LLMResponse(BaseModel):
    """Standardized response from any LLM provider."""
    # content is retained for compatibility. It must always contain final user-
    # visible content, never hidden reasoning/thinking text.
    content: str = ""
    final_content: str = ""
    reasoning_present: bool = False
    reasoning_token_count: int = 0
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int | None = 0
    total_tokens: int = 0
    finish_reason: str = ""
    latency_ms: int = 0
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    raw_response: dict[str, Any] = Field(default_factory=dict)


# --- Processing Event ---

class ProcessingEvent(BaseModel):
    """A log event in the processing pipeline."""
    event_id: str
    project_id: str
    article_id: str | None = None
    event_type: str = ""
    agent_name: str = ""
    skill_name: str = ""
    message: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


# --- Ingestion ---

class IngestionResult(BaseModel):
    """Result of a file or DOI import operation."""
    article_id: str
    resource_id: str | None = None
    resource_type: ResourceType | None = None
    file_name: str = ""
    local_path: str = ""
    file_hash: str = ""
    file_size: int = 0
    is_duplicate: bool = False
    doi: str | None = None
    url: str | None = None
    title: str = ""
    status: str = "success"
    error: str | None = None


class ExtractionResult(BaseModel):
    """Result of LLM extraction from a single table/sheet."""
    table_id: str = ""
    article_id: str
    resource_id: str
    source_type: str = ""
    sheet_name: str | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)
    columns: list[CandidateColumn] = Field(default_factory=list)
    matched_fields: dict[str, str] = Field(default_factory=dict)
    row_count: int = 0
    confidence: float = 0.0
    llm_call_ids: list[str] = Field(default_factory=list)
    status: str = "success"
    error: str | None = None
