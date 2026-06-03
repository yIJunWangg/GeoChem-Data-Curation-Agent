"""User target header model with display units and semantic groups."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..core.header_descriptions import load_header_descriptions
from ..core.schema_manager import SchemaManager
from .header_normalizer import HeaderNormalizer


@dataclass(frozen=True)
class TargetHeader:
    """A user-facing output header with canonical mapping metadata."""

    display_header: str
    source_header: str
    canonical_field: str
    description: str
    target_unit: str
    field_group: str


class TargetHeaderBuilder:
    """Build target headers from Excel/source headers and header descriptions."""

    def __init__(self, schema_manager: SchemaManager | None = None):
        self.schema_manager = schema_manager
        self.normalizer = HeaderNormalizer()

    def build(
        self,
        source_headers: list[str],
        descriptions_path: str | Path | None = None,
    ) -> list[TargetHeader]:
        descriptions = load_header_descriptions(descriptions_path) if descriptions_path else {}
        result = []
        for source in source_headers:
            if source is None or str(source).strip() == "":
                continue
            normalized = self.normalizer.normalize(str(source))
            canonical = self._canonical_field(str(source), normalized.normalized_key)
            description = self._description_for(str(source), normalized.clean, canonical, descriptions)
            display = self._display_header(str(source), normalized.unit)
            result.append(TargetHeader(
                display_header=display,
                source_header=str(source),
                canonical_field=canonical,
                description=description,
                target_unit=normalized.unit,
                field_group=classify_field(canonical, display),
            ))
        return result

    def _canonical_field(self, header: str, normalized_key: str) -> str:
        if self.schema_manager:
            for variant in self.normalizer.variants(header):
                field, conf = self.schema_manager.match_field(variant)
                if field and conf >= 0.9:
                    return field
        return normalized_key

    def _description_for(self, raw: str, clean: str, canonical: str, descriptions: dict[str, str]) -> str:
        candidates = [
            raw,
            raw.replace("\n", " "),
            clean,
            canonical,
            self.normalizer.normalized_key(raw),
        ]
        for key in candidates:
            if key in descriptions:
                return descriptions[key]
        raw_norm = self.normalizer.normalized_key(raw)
        for key, desc in descriptions.items():
            key_norm = self.normalizer.normalized_key(key)
            if key_norm == raw_norm or key_norm == canonical:
                return desc
        return ""

    def _display_header(self, raw: str, unit: str) -> str:
        text = " ".join(str(raw).split())
        if "\n" in raw:
            return text
        return text


def classify_field(canonical_field: str, display_header: str = "") -> str:
    """Classify a target field into a stable mapping group."""
    name = canonical_field
    display = display_header
    if name in {"SampleID", "Sample_ID", "FirstAuthor", "Year", "Title", "Reference", "DOI", "DataSource"}:
        return "basic_info"
    if name in {"Latitude", "Longitude", "Location1-detail", "Location2-country", "SiteName", "Paleolatitude", "Paleolongitude"}:
        return "location"
    if name in {"Depth/m", "Depth", "深度说明", "Age", "Age_min", "Age_max", "说明", "RelativeDepth", "Formation", "Unit", "Era", "Period", "Epoch", "Stage"}:
        return "stratigraphy_age"
    if name in {"Lithology", "LithType", "MetamorphicGrade", "Setting", "原文", "WaterDepthest", "Note", "SampleName", "Material", "DryDensity", "Unnamed_123"}:
        return "sample_context"
    if name in {
        "δ15Nbulk", "δ15Nsil", "δ15Nchlorin", "δ15Nker", "δ13Corg", "δ13Ccarb",
        "δ18Ocarb", "δ34Spy", "d15Nbulk", "d15Nsil", "d15Nchlorin", "d15Nker",
        "d13Corg", "d13Ccarb", "d18Ocarb", "d34Spy", "TN", "TOC", "TS", "C_N_mol", "CaCO3", "TC",
        "TIC", "OI", "HI", "Tmax", "S1", "S2", "S3", "C37",
    }:
        return "isotope_organic"
    if name in {"Fepy", "FeS2", "Fehr", "Fecarb", "Feox", "Femag", "Fepy_Fehr", "Fehr_Fet", "Fecarb_Fet", "Feox_Fet"}:
        return "iron_speciation"
    if name in {"CIA", "CIAcorr", "PIA", "CIW", "WIP"}:
        return "weathering_indices"
    ree = {"REE_total", "LREE", "HREE", "La", "Ce", "Pr", "Nd", "Sm", "Eu", "Gd", "Tb", "Dy", "Ho", "Er", "Tm", "Yb", "Lu", "Y"}
    if name in ree or "REE" in display or "LREE" in display or "HREE" in display:
        return "ree"
    major = {
        "Al2O3", "CaO", "K2O", "Na2O", "P2O5", "SiO2", "TiO2", "TFe2O3",
        "MnO", "MgO", "Fe2O3", "FeO", "CaO_corr", "Cr2O3", "P", "Al",
        "K", "Si", "Ca", "Ti", "Na", "Mg", "Fe", "LOI", "S",
    }
    if name in major:
        return "major_elements"
    return "trace_elements"
