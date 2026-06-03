"""Schema management: load, validate, query geochem schema."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from .exceptions import SchemaValidationError
from .logging_config import get_logger
from .models import ChemicalForm, GeoChemSchema, SchemaField

logger = get_logger("schema")


def normalize_unicode(text: str) -> str:
    """Normalize Unicode: convert subscripts/superscripts to ASCII."""
    replacements = {
        "₀": "0", "₁": "1", "₂": "2", "₃": "3",
        "₄": "4", "₅": "5", "₆": "6", "₇": "7",
        "₈": "8", "₉": "9",
        "⁰": "0", "¹": "1", "²": "2", "³": "3",
        "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7",
        "⁸": "8", "⁹": "9",
        "₊": "+", "₋": "-",
        "α": "alpha", "β": "beta", "γ": "gamma",
        "δ": "delta", "ε": "epsilon", "ρ": "rho",
        "σ": "sigma", "τ": "tau",
        "₂": "2",  # subscript 2 (redundant but explicit for common case)
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def normalize_field_name(name: str) -> str:
    """Normalize a field name for matching."""
    name = normalize_unicode(name)
    name = name.strip()
    name = re.sub(r"\s+", " ", name)
    return name


class SchemaManager:
    """Manage geochem schema: load, validate, query fields and aliases."""

    def __init__(self):
        self.schema: GeoChemSchema | None = None
        self._alias_map: dict[str, str] = {}  # normalized_alias -> field_name
        self._field_map: dict[str, SchemaField] = {}  # field_name -> SchemaField

    def load_from_file(self, path: str | Path) -> GeoChemSchema:
        """Load schema from a YAML file."""
        path = Path(path)
        if not path.exists():
            raise SchemaValidationError(f"Schema file not found: {path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data or "columns" not in data:
            raise SchemaValidationError("Schema file must contain 'columns' key")

        self.schema = GeoChemSchema(**data)
        self._build_index()
        logger.info(f"Loaded schema with {len(self.schema.columns)} fields from {path}")
        return self.schema

    def load_from_dict(self, data: dict) -> GeoChemSchema:
        """Load schema from a dictionary."""
        self.schema = GeoChemSchema(**data)
        self._build_index()
        return self.schema

    def _build_index(self) -> None:
        """Build alias and field lookup indices."""
        self._alias_map.clear()
        self._field_map.clear()
        if not self.schema:
            return

        for field in self.schema.columns:
            self._field_map[field.name] = field
            # Index the field name itself
            normalized = normalize_field_name(field.name).lower()
            self._alias_map[normalized] = field.name
            # Index all aliases
            for alias in field.aliases:
                normalized_alias = normalize_field_name(alias).lower()
                self._alias_map[normalized_alias] = field.name

    def get_field(self, name: str) -> SchemaField | None:
        """Get a schema field by exact name."""
        if not self.schema:
            return None
        return self._field_map.get(name)

    def match_field(self, raw_name: str) -> tuple[str | None, float]:
        """Try to match a raw column name to a schema field.

        Returns (field_name, confidence) or (None, 0.0) if no match.
        """
        if not self.schema or not raw_name.strip():
            return None, 0.0

        normalized = normalize_field_name(raw_name).lower()

        # Exact match
        if normalized in self._alias_map:
            return self._alias_map[normalized], 1.0

        # Try matching after removing spaces and common prefixes
        cleaned = re.sub(r"[\s_\-]+", "", normalized)
        for alias_norm, field_name in self._alias_map.items():
            alias_cleaned = re.sub(r"[\s_\-]+", "", alias_norm)
            if cleaned == alias_cleaned:
                return field_name, 0.95

        # Partial match: only if raw_name is long enough to be meaningful
        if len(normalized) >= 3:
            best_match = None
            best_score = 0.0
            for field in self.schema.columns:
                fname_lower = field.name.lower()
                if fname_lower in normalized or normalized in fname_lower:
                    # Score based on how much of the longer string is covered
                    longer = max(len(normalized), len(fname_lower))
                    shorter = min(len(normalized), len(fname_lower))
                    score = shorter / longer
                    if score > best_score:
                        best_score = score
                        best_match = field.name

            if best_match and best_score > 0.5:
                return best_match, min(best_score * 0.9, 0.99)

        return None, 0.0

    def get_all_field_names(self) -> list[str]:
        """Get all field names in the schema."""
        if not self.schema:
            return []
        return self.schema.get_field_names()

    def get_all_aliases(self) -> dict[str, list[str]]:
        """Get a mapping of field_name -> list of aliases."""
        if not self.schema:
            return {}
        return {f.name: f.aliases for f in self.schema.columns}
