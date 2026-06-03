"""LLM-driven column mapping + programmatic data extraction."""

from __future__ import annotations

import json
import re
from typing import Any

from ..core.exceptions import ExtractionFailedError
from ..core.logging_config import get_logger
from ..core.models import ExtractionResult, LLMResponse
from ..core.schema_manager import SchemaManager
from ..curation.header_normalizer import HeaderNormalizer
from ..providers.llm_client import LLMClient
from .base_reader import RawContent

logger = get_logger("extractors.llm_extractor")

SYSTEM_PROMPT = """You are a geochemistry data column mapper. Your task is to find which table column headers correspond to each schema field.

Rules:
1. For each schema field, find the best matching table header (if any).
2. Consider Unicode variations (subscripts like ₂ → 2), unit suffixes, abbreviations.
3. If no table header matches a schema field, set its value to null.
4. Output ONLY a valid JSON object — no markdown, no commentary, no code fences.
5. The JSON object keys MUST be schema field names, values are the matching table headers (or null).
6. Return exactly one entry per schema field — do not add extra entries.
7. You are given article context, target schema field descriptions, and table headers only.
8. Do NOT rely on data rows or sample values; no row values are provided to you."""

USER_PROMPT_TEMPLATE = """Paper: {title}
DOI: {doi}
Article context:
{article_context}

Table: {table_info} ({row_count} rows, {col_count} columns)

--- TABLE HEADERS ---
{header_list}
--- END HEADERS ---

--- SCHEMA FIELDS ---
{field_list}
--- END FIELDS ---

For each schema field, find the matching table header. Return a JSON object where:
- Keys are schema field names
- Values are the matching table header (or null if no match)

Example: {{"Sample_ID": "SampleID", "SiO2": "SiO₂(wt%)", "Al2O3": null}}

Return ONLY the JSON object. No other text."""


class LLMExtractor:
    """Extract structured geochemical data using LLM column mapping + programmatic extraction."""

    def __init__(self, llm_client: LLMClient, schema_manager: SchemaManager, header_descriptions: dict[str, str] | None = None):
        self.llm_client = llm_client
        self.schema_manager = schema_manager
        self.header_descriptions = header_descriptions or {}
        self.normalizer = HeaderNormalizer()

    def extract(
        self,
        content: RawContent,
        article_metadata: dict | None = None,
        project_id: str = "",
        article_id: str = "",
        resource_id: str = "",
    ) -> ExtractionResult:
        """Extract data from a single RawContent.

        1. Build prompt with headers + schema field descriptions
        2. Call LLM to get column mapping
        3. Extract data programmatically using the mapping
        4. Return ExtractionResult
        """
        schema_fields = self.schema_manager.get_all_field_names()
        if not schema_fields:
            return ExtractionResult(
                article_id=article_id,
                resource_id=resource_id,
                status="error",
                error="No schema fields loaded",
            )

        # Step 1: LLM column mapping
        try:
            messages = self._build_messages(content, schema_fields, article_metadata)
            response = self._call_llm(messages, project_id, article_id)
            mapping = self._parse_mapping_response(response, content.headers, schema_fields)
        except Exception as e:
            logger.error(f"Column mapping failed: {e}")
            return ExtractionResult(
                article_id=article_id,
                resource_id=resource_id,
                source_type=content.source_type,
                sheet_name=content.sheet_name,
                status="error",
                error=str(e),
            )

        # Step 2: Programmatic data extraction
        rows = self._extract_rows(content, mapping)

        # Step 3: Compute matched fields and confidence
        matched_fields = {h: f for h, f in mapping.items() if f is not None}
        confidence = self._compute_confidence(rows, schema_fields)

        status = "success" if rows else "error"
        error_msg = None if rows else "No rows extracted (all rows may lack SampleID)"

        return ExtractionResult(
            article_id=article_id,
            resource_id=resource_id,
            source_type=content.source_type,
            sheet_name=content.sheet_name,
            rows=rows,
            matched_fields=matched_fields,
            row_count=len(rows),
            confidence=confidence,
            status=status,
            error=error_msg,
        )

    def _build_messages(
        self,
        content: RawContent,
        schema_fields: list[str],
        article_metadata: dict | None,
    ) -> list[dict[str, str]]:
        """Build LLM messages with headers only (no row data)."""
        # Build header list with unit info
        header_lines = []
        for h in content.headers:
            unit = content.header_units.get(h, "")
            if unit:
                header_lines.append(f"- {h} ({unit})")
            else:
                header_lines.append(f"- {h}")
        header_list = "\n".join(header_lines)

        # Build field list with descriptions
        field_lines = []
        description_overrides = self._description_overrides()
        for f in schema_fields:
            field_obj = self.schema_manager.get_field(f)
            if field_obj:
                desc = description_overrides.get(f) or field_obj.description or f
                unit = f" (default unit: {field_obj.default_unit})" if field_obj.default_unit else ""
                field_lines.append(f"- {f}: {desc}{unit}")
            else:
                field_lines.append(f"- {f}")
        field_list = "\n".join(field_lines)

        # Article metadata
        meta = article_metadata or {}
        title = meta.get("title", content.title or "Unknown")
        doi = meta.get("doi", "")
        article_context = meta.get("context", "")
        if not article_context:
            context_parts = []
            for key in ("reference", "authors", "year", "journal"):
                value = meta.get(key)
                if value:
                    context_parts.append(f"{key}: {value}")
            article_context = "\n".join(context_parts) or "No article text or abstract provided."

        # Table info
        table_info = content.sheet_name or content.title or "data table"

        user_prompt = USER_PROMPT_TEMPLATE.format(
            title=title,
            doi=doi,
            article_context=article_context,
            table_info=table_info,
            row_count=content.row_count,
            col_count=len(content.headers),
            header_list=header_list,
            field_list=field_list,
        )

        return [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

    def _description_overrides(self) -> dict[str, str]:
        overrides: dict[str, str] = {}
        for raw_header, desc in self.header_descriptions.items():
            for variant in self.normalizer.variants(raw_header):
                field, conf = self.schema_manager.match_field(variant)
                if field and conf >= 0.9 and field not in overrides:
                    overrides[field] = desc
        return overrides

    def _call_llm(
        self, messages: list[dict[str, str]], project_id: str, article_id: str
    ) -> LLMResponse:
        """Call LLM via LLMClient.chat()."""
        return self.llm_client.chat(
            messages=messages,
            task_name="data_extraction",
            project_id=project_id,
            article_id=article_id,
            agent_name="extractor",
            skill_name="column_mapping",
            use_cache=True,
        )

    def _parse_mapping_response(
        self, response: LLMResponse, valid_headers: list[str], schema_fields: list[str] | None = None
    ) -> dict[str, str | None]:
        """Parse LLM response as a mapping dict {raw_header: schema_field}.

        LLM returns: {"SchemaField": "table_header", ...}
        We invert to: {"table_header": "SchemaField", ...}

        Returns dict where keys are original table headers, values are schema field names or None.
        """
        text = response.content.strip()
        if not text:
            raise ExtractionFailedError("Empty LLM response")

        # Strip markdown code fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
            text = text.strip()

        # Find JSON object boundaries
        start = text.find("{")
        if start == -1:
            raise ExtractionFailedError(f"No JSON object found in LLM response: {text[:200]}")

        text = text[start:]

        # Find the last } to bound the object
        end = text.rfind("}")
        if end != -1:
            text = text[:end + 1]

        # Try parsing
        mapping = self._try_parse_json(text)
        if mapping is None:
            mapping = self._try_parse_truncated_json_object(text)

        if mapping is None or not isinstance(mapping, dict):
            raise ExtractionFailedError(f"Cannot parse LLM response as JSON object: {text[:200]}")

        # LLM returns {schema_field: table_header}
        # Invert to {table_header: schema_field}
        valid_header_set = set(valid_headers)
        schema_field_set = set(schema_fields) if schema_fields else None
        result: dict[str, str | None] = {h: None for h in valid_headers}

        for schema_field, table_header in mapping.items():
            if table_header is None or not isinstance(table_header, str) or not table_header.strip():
                continue
            table_header = table_header.strip()
            # Validate the schema field exists
            if schema_field_set and schema_field not in schema_field_set:
                logger.debug(f"LLM returned unknown schema field: {schema_field}")
                continue
            # Validate the table header exists in our headers
            if table_header in valid_header_set:
                result[table_header] = schema_field
            else:
                # Try fuzzy match: check if table_header is a substring or close match
                for h in valid_headers:
                    if h.lower() == table_header.lower() or h.strip() == table_header.strip():
                        result[h] = schema_field
                        break

        # Log mapping summary
        mapped_count = sum(1 for v in result.values() if v is not None)
        logger.info(f"Column mapping: {mapped_count}/{len(valid_headers)} headers mapped")
        for h, f in result.items():
            if f is not None:
                logger.debug(f"  {h} → {f}")

        return result

    def _extract_rows(
        self, content: RawContent, mapping: dict[str, str | None]
    ) -> list[dict]:
        """Extract data rows programmatically using the column mapping.

        For each raw row, map cell values to schema fields using the mapping.
        Only rows with a non-empty sample ID field are kept.
        """
        if not content.raw_rows:
            logger.warning("No raw_rows available for programmatic extraction")
            return []

        # Find the sample ID field name from the mapping
        sample_id_field = self._find_sample_id_field(mapping)

        headers = content.headers
        rows = []

        for raw_row in content.raw_rows:
            row: dict[str, Any] = {}
            for i, header in enumerate(headers):
                schema_field = mapping.get(header)
                if schema_field is None:
                    continue
                if i < len(raw_row):
                    value = raw_row[i]
                    if value is not None and str(value).strip():
                        row[schema_field] = value
                    else:
                        row[schema_field] = None

            # Only keep rows with a non-empty sample ID
            if sample_id_field and row.get(sample_id_field):
                rows.append(row)
            elif not sample_id_field:
                # No sample ID field mapped — keep all non-empty rows
                if any(v is not None for v in row.values()):
                    rows.append(row)

        logger.info(f"Programmatic extraction: {len(rows)} rows from {len(content.raw_rows)} raw rows")
        return rows

    def _find_sample_id_field(self, mapping: dict[str, str | None]) -> str | None:
        """Find the sample ID field name from the mapping values."""
        sample_id_variants = {"SampleID", "Sample_ID", "Sample_No", "Sample_no", "SampleNo"}
        for header, schema_field in mapping.items():
            if schema_field and schema_field in sample_id_variants:
                return schema_field
        return None

    def _try_parse_json(self, text: str) -> dict | list | None:
        """Try to parse JSON text. Returns None on failure."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            repaired = self._repair_json(text)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                return None

    def _try_parse_truncated_json_object(self, text: str) -> dict | None:
        """Attempt to parse truncated JSON object by closing open structures."""
        # Find the last key-value pair that's complete
        # Look for the last `"key": value` pattern that ends properly
        depth = 0
        last_complete_end = -1
        in_string = False
        escape_next = False

        for i, ch in enumerate(text):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\':
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    last_complete_end = i
                    break

        if last_complete_end != -1:
            # Already complete
            try:
                return json.loads(text[:last_complete_end + 1])
            except json.JSONDecodeError:
                pass

        # Try to close the object
        truncated = text.rstrip().rstrip(",")
        truncated += "}"
        try:
            return json.loads(truncated)
        except json.JSONDecodeError:
            repaired = self._repair_json(truncated)
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                return None

    def _repair_json(self, text: str) -> str:
        """Attempt to repair common JSON issues."""
        # Remove trailing commas before } or ]
        text = re.sub(r",\s*([}\]])", r"\1", text)
        # Remove single-line comments
        text = re.sub(r"//[^\n]*", "", text)
        # Fix single quotes to double quotes (only if no double quotes present)
        if '"' not in text:
            text = text.replace("'", '"')
        return text

    def _compute_confidence(self, rows: list[dict], schema_fields: list[str]) -> float:
        """Compute extraction confidence based on fill rate and consistency."""
        if not rows:
            return 0.0

        # Calculate fill rate (non-null values / expected values)
        total_cells = len(rows) * len(schema_fields)
        filled_cells = 0
        for row in rows:
            for field in schema_fields:
                val = row.get(field)
                if val is not None and val != "":
                    filled_cells += 1

        fill_rate = filled_cells / total_cells if total_cells > 0 else 0.0

        # Check sample ID presence (try common field names)
        sample_id_variants = {"SampleID", "Sample_ID", "Sample_No", "Sample_no", "SampleNo"}
        sid_field = next((f for f in schema_fields if f in sample_id_variants), None)
        if sid_field:
            sample_id_present = sum(1 for r in rows if r.get(sid_field)) / len(rows)
        else:
            sample_id_present = 1.0  # No sample ID field → assume OK

        # Combined confidence
        confidence = (fill_rate * 0.6) + (sample_id_present * 0.4)

        return round(min(confidence, 1.0), 3)
