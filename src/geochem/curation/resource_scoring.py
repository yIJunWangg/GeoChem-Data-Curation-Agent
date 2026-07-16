"""Explainable resource relevance scoring for article workbench elements."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


NEGATIVE_SECTIONS = (
    "references", "bibliography", "acknowledgements", "acknowledgments",
    "funding", "conflict of interest", "data availability", "credit author",
)
GENERIC_HEADERS = {"sample", "samples", "sampleid", "sample id", "age", "formation", "reference", "references"}
GEO_TERMS = re.compile(
    r"\b(ppm|ppb|mg/kg|ug/g|wt\s*%|geochem\w*|isotope\w*|concentration\w*|contents?|toc|tn|ts|ree|rare earth|oxide|icp-ms|xrf)\b|‰|%",
    re.I,
)
METHOD_TERMS = re.compile(r"\b(methods?|analytical|analysis|measured|determined|samples? were|collected|processed)\b", re.I)
REFERENCE_CITATION_DENSITY = re.compile(r"\b[A-Z][A-Za-z-]+(?:\s+et\s+al\.)?\s*\(\d{4}[a-z]?\)")


@dataclass
class ScoreResult:
    score: float
    matched_headers: list[str]
    reasons: list[str] = field(default_factory=list)


class ResourceScoringEngine:
    """Deterministic, structure-aware score for candidate resources."""

    def score(
        self,
        *,
        element_type: str,
        text: str,
        caption: str = "",
        section_path: str = "",
        matched_headers: list[str] | None = None,
        raw_table: dict[str, Any] | None = None,
        user_labels: list[str] | None = None,
    ) -> ScoreResult:
        matched_headers = list(matched_headers or [])
        raw_table = raw_table or {}
        haystack = " ".join([text or "", caption or "", " ".join(raw_table.get("headers") or [])])
        lower = haystack.lower()
        reasons: list[str] = []

        base = {"table": 0.45, "figure": 0.24, "paragraph": 0.20}.get(element_type, 0.18)
        positive = base
        negative = 0.0
        reasons.append(f"基础分 {base:.2f} ({element_type})")

        non_generic = [h for h in matched_headers if self._header_key(h) not in GENERIC_HEADERS]
        generic = [h for h in matched_headers if self._header_key(h) in GENERIC_HEADERS]
        if non_generic:
            bonus = min(0.26, len(non_generic) * 0.055)
            positive += bonus
            reasons.append(f"命中专业表头 {len(non_generic)} 个 +{bonus:.2f}")
        if generic:
            bonus = min(0.04, len(generic) * 0.01)
            positive += bonus
            reasons.append(f"泛表头仅弱加分 {len(generic)} 个 +{bonus:.2f}")

        geo_hit = bool(GEO_TERMS.search(haystack))
        numeric_density = self._numeric_density(haystack)
        table_rows = len(raw_table.get("rows") or [])
        table_cols = max(len(raw_table.get("headers") or []), max((len(row) for row in raw_table.get("rows") or []), default=0))
        if geo_hit:
            positive += 0.12
            reasons.append("包含单位/地化术语 +0.12")
        if numeric_density >= 0.08:
            positive += 0.08
            reasons.append("数字密度较高 +0.08")
        if element_type == "table" and table_rows >= 2 and table_cols >= 2:
            positive += 0.14
            reasons.append("具有表格结构 +0.14")
        if element_type == "paragraph" and METHOD_TERMS.search(haystack) and geo_hit:
            positive += 0.06
            reasons.append("方法/样品描述伴随地化术语 +0.06")

        section_lower = section_path.lower()
        negative_section = any(label in section_lower for label in NEGATIVE_SECTIONS)
        if negative_section:
            negative += 0.45
            reasons.append("位于参考文献/致谢/数据可用性等排除章节 -0.45")
        if REFERENCE_CITATION_DENSITY.search(haystack) and len(REFERENCE_CITATION_DENSITY.findall(haystack)) >= 3:
            negative += 0.18
            reasons.append("引用密度高，疑似参考文献段落 -0.18")
        if "不是数据" in (user_labels or []) or "参考文献/排除" in (user_labels or []):
            negative += 0.55
            reasons.append("用户标记为排除/不是数据 -0.55")
        if "包含数据" in (user_labels or []):
            positive += 0.35
            reasons.append("用户标记包含数据 +0.35")
        elif "可能包含数据" in (user_labels or []):
            positive += 0.18
            reasons.append("用户标记可能包含数据 +0.18")

        score = max(0.02, min(0.98, positive - negative))
        if negative_section:
            cap = 0.35 if element_type in {"table", "figure"} else 0.15
            if score > cap:
                score = cap
            reasons.append(f"排除章节硬上限 {cap:.2f}")
        if not non_generic and not geo_hit and numeric_density < 0.04 and element_type == "paragraph":
            score = min(score, 0.42)
            reasons.append("缺少专业地化证据，段落上限 0.42")
        return ScoreResult(round(score, 4), matched_headers, reasons)

    def _numeric_density(self, text: str) -> float:
        tokens = re.findall(r"\S+", text or "")
        if not tokens:
            return 0.0
        numeric = sum(1 for token in tokens if re.search(r"\d", token))
        return numeric / max(1, len(tokens))

    def _header_key(self, value: str) -> str:
        cleaned = re.sub(r"\s*(?:\(|（)?\s*(?:ppm|ppb|wt\s*%|%|‰|mg/kg|ug/g|m)\s*(?:\)|）)?\s*$", "", str(value), flags=re.I)
        return re.sub(r"[^a-z0-9]+", " ", cleaned.lower()).strip()
