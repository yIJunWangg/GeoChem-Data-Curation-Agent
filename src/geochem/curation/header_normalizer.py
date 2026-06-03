"""Header normalization helpers used before schema mapping."""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..core.schema_manager import normalize_unicode


@dataclass(frozen=True)
class NormalizedHeader:
    """Normalized representation of a source table header."""

    raw: str
    clean: str
    normalized_key: str
    unit: str = ""


class HeaderNormalizer:
    """Normalize geochemical table headers without using an LLM."""

    _KNOWN_UNITS = {
        "%", "wt%", "ppm", "ppb", "ppt", "mg/kg", "mg/g", "ug/g", "μg/g",
        "ng/g", "‰", "mg CO2/g TOC", "mg HC/g TOC", "mg HC/g Rock",
        "mg CO2/g Rock", "nmol/g", "g/cm3", "g/cm³", "C", "°C", "m",
    }

    _SPECIAL_KEYS = {
        "age min": "Age_min",
        "age_min": "Age_min",
        "age(max)": "Age_max",
        "age max": "Age_max",
        "age_max": "Age_max",
        "c/nmol": "C_N_mol",
        "c/n mol": "C_N_mol",
        "c/n": "C_N_mol",
        "fepy/fehr": "Fepy_Fehr",
        "fehr/fet": "Fehr_Fet",
        "fecarb/fet": "Fecarb_Fet",
        "feox/fet": "Feox_Fet",
        "dry density": "DryDensity",
        "dry_density": "DryDensity",
    }

    def normalize(self, header: str, known_unit: str = "") -> NormalizedHeader:
        raw = "" if header is None else str(header).strip()
        clean, unit = self.split_unit(raw)
        if known_unit and not unit:
            unit = known_unit
        key = self.normalized_key(clean)
        return NormalizedHeader(raw=raw, clean=clean, normalized_key=key, unit=unit)

    def split_unit(self, header: str) -> tuple[str, str]:
        """Split a header into name and unit, preserving ratios like Fepy/Fehr."""
        text = self._normalize_punctuation(normalize_unicode(header.strip().replace("δ", "d")))
        if not text:
            return "", ""

        if "\n" in text:
            parts = [p.strip() for p in text.split("\n") if p.strip()]
            if len(parts) >= 2 and self._is_unit(parts[-1]):
                return " ".join(parts[:-1]).strip(), parts[-1]

        match = re.search(r"\(([^)]+)\)\s*$", text)
        if match and self._is_unit(match.group(1).strip()):
            return text[:match.start()].strip(), match.group(1).strip()

        match = re.search(r"/\s*([A-Za-z%‰°]+)\s*$", text)
        if match and self._is_unit(match.group(1).strip()):
            return text[:match.start()].strip(), match.group(1).strip()

        return text, ""

    def normalized_key(self, text: str) -> str:
        text = self._normalize_punctuation(normalize_unicode(text.strip().replace("δ", "d")))
        text = re.sub(r"\s+", " ", text)
        lookup = text.lower()
        lookup = lookup.replace("（", "(").replace("）", ")")
        lookup = re.sub(r"\s*\(\s*", " ", lookup)
        lookup = re.sub(r"\s*\)\s*", "", lookup).strip()
        if lookup in self._SPECIAL_KEYS:
            return self._SPECIAL_KEYS[lookup]

        if "/" in text:
            ratio_key = text.replace("/", "_")
            if ratio_key in {"Fepy_Fehr", "Fehr_Fet", "Fecarb_Fet", "Feox_Fet"}:
                return ratio_key

        # Keep schema-friendly chemical names readable, but remove separators.
        key = re.sub(r"[\s\-]+", "_", text)
        key = key.replace("（", "_").replace("）", "").replace("(", "_").replace(")", "")
        key = re.sub(r"_+", "_", key).strip("_")
        return key

    def variants(self, header: str, known_unit: str = "") -> list[str]:
        normalized = self.normalize(header, known_unit)
        variants = [
            normalized.raw,
            normalized.clean,
            normalized.normalized_key,
            normalized.clean.replace("δ", "d"),
            normalized.clean.replace("d", "δ", 1) if normalized.clean.startswith("d") else normalized.clean,
        ]
        if normalized.unit:
            variants.append(f"{normalized.clean} ({normalized.unit})")
            variants.append(f"{normalized.clean}\n{normalized.unit}")
        seen = set()
        result = []
        for item in variants:
            if item and item not in seen:
                seen.add(item)
                result.append(item)
        return result

    def _normalize_punctuation(self, text: str) -> str:
        return (
            text.replace("（", "(")
            .replace("）", ")")
            .replace("％", "%")
            .replace("μ", "u")
        )

    def _is_unit(self, text: str) -> bool:
        if not text:
            return False
        if text in self._KNOWN_UNITS:
            return True
        lowered = text.lower()
        if lowered in {u.lower() for u in self._KNOWN_UNITS}:
            return True
        return any(marker in text for marker in ("%", "‰", "°"))
