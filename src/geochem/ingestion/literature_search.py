"""Public scholarly literature search for the local GeoChem agent.

This is deliberately a narrow, auditable alternative to a browser-search MCP:
it queries scholarly metadata indexes only and never downloads an article.  A
user must select a result before :class:`OpenAccessResolver` attempts the
separate public-PDF acquisition flow.
"""

from __future__ import annotations

from typing import Any

import requests


class LiteratureSearchService:
    """Search Crossref and OpenAlex and return one normalized result shape."""

    USER_AGENT = "GeoChem Data Curation Agent/1.0 (literature-search)"

    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", self.USER_AGENT)
        self.session.headers.setdefault("Accept", "application/json")

    def search(self, query: str, limit: int = 8, sort_mode: str = "relevance") -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("请输入要检索的研究主题或关键词。")
        limit = max(1, min(int(limit), 20))
        sort_mode = sort_mode if sort_mode in {"relevance", "latest"} else "relevance"
        results: list[dict[str, Any]] = []
        errors: list[str] = []
        providers: list[str] = []
        for provider, loader in (("crossref", self._crossref), ("openalex", self._openalex)):
            try:
                results.extend(loader(query, limit, sort_mode))
                providers.append(provider)
            except requests.RequestException as exc:
                errors.append(f"{provider}: {exc}")
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"{provider}: 无法解析返回结果（{exc}）")
        merged = self._dedupe(results, sort_mode)
        return {
            "query": query,
            "sort_mode": sort_mode,
            "results": merged[:limit],
            "providers": providers,
            "errors": errors,
        }

    def _crossref(self, query: str, limit: int, sort_mode: str = "relevance") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"query.bibliographic": query, "filter": "type:journal-article", "rows": limit}
        if sort_mode == "latest":
            params.update({"sort": "published", "order": "desc"})
        response = self.session.get(
            "https://api.crossref.org/works",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("message", {}).get("items", [])
        return [self._crossref_item(item) for item in items if item.get("title")]

    def _openalex(self, query: str, limit: int, sort_mode: str = "relevance") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"search": query, "per-page": limit, "filter": "type:article"}
        if sort_mode == "latest":
            params["sort"] = "publication_date:desc"
        response = self.session.get(
            "https://api.openalex.org/works",
            params=params,
            timeout=15,
        )
        response.raise_for_status()
        items = response.json().get("results", [])
        return [self._openalex_item(item) for item in items if item.get("title")]

    @staticmethod
    def _authors(authors: list[dict[str, Any]]) -> list[str]:
        names = []
        for author in authors:
            name = " ".join(part for part in [author.get("given", ""), author.get("family", "")] if part).strip()
            if name:
                names.append(name)
        return names

    @staticmethod
    def _year(parts: Any) -> int | None:
        try:
            return int(parts[0][0])
        except (IndexError, TypeError, ValueError):
            return None

    def _crossref_item(self, item: dict[str, Any]) -> dict[str, Any]:
        doi = str(item.get("DOI") or "").lower()
        title = str((item.get("title") or [""])[0])
        year = self._year((item.get("published-print") or item.get("published-online") or item.get("issued") or {}).get("date-parts"))
        venue = str((item.get("container-title") or [""])[0])
        return self._result(
            source="Crossref", source_id=doi or str(item.get("URL") or title), doi=doi, title=title,
            authors=self._authors(item.get("author") or []), year=year, venue=venue,
            landing_url=str(item.get("URL") or (f"https://doi.org/{doi}" if doi else "")),
            open_access=None, cited_by=int(item.get("is-referenced-by-count") or 0),
        )

    def _openalex_item(self, item: dict[str, Any]) -> dict[str, Any]:
        doi = str(item.get("doi") or "").removeprefix("https://doi.org/").lower()
        primary = item.get("primary_location") or {}
        oa = item.get("open_access") or {}
        best_oa = item.get("best_oa_location") or {}
        authors = [entry.get("author", {}) for entry in item.get("authorships") or []]
        return self._result(
            source="OpenAlex", source_id=str(item.get("id") or doi or item.get("title") or ""), doi=doi,
            title=str(item.get("title") or ""), authors=[str(author.get("display_name") or "") for author in authors if author.get("display_name")],
            year=int(item["publication_year"]) if item.get("publication_year") else None,
            venue=str((primary.get("source") or {}).get("display_name") or ""),
            landing_url=str((primary.get("landing_page_url") or item.get("doi") or "")),
            pdf_url=str(best_oa.get("pdf_url") or ""), open_access=bool(oa.get("is_oa")),
            cited_by=int(item.get("cited_by_count") or 0),
        )

    @staticmethod
    def _result(*, source: str, source_id: str, doi: str, title: str, authors: list[str], year: int | None,
                venue: str, landing_url: str, open_access: bool | None, cited_by: int = 0,
                pdf_url: str = "") -> dict[str, Any]:
        return {
            "literature_id": f"LIT_{source_id.encode('utf-8').hex()[:24].upper()}",
            "source": source,
            "doi": doi,
            "title": title,
            "authors": authors[:12],
            "year": year,
            "venue": venue,
            "landing_url": landing_url,
            "pdf_url": pdf_url,
            "open_access": open_access,
            "cited_by_count": cited_by,
        }

    @staticmethod
    def _dedupe(items: list[dict[str, Any]], sort_mode: str = "relevance") -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for item in items:
            title_key = " ".join(str(item.get("title") or "").lower().split())
            key = str(item.get("doi") or title_key)
            if not key:
                continue
            current = unique.get(key)
            if not current:
                unique[key] = item
                continue
            # Preserve source diversity, but prefer the record that includes an
            # openly accessible PDF and richer citation metadata.
            if item.get("pdf_url") and not current.get("pdf_url"):
                unique[key] = {**current, **item, "source": f"{current['source']} + {item['source']}"}
            else:
                current["source"] = f"{current['source']} + {item['source']}" if item["source"] not in current["source"] else current["source"]
                current["open_access"] = bool(current.get("open_access") or item.get("open_access"))
        if sort_mode == "latest":
            return sorted(unique.values(), key=lambda item: (-(int(item.get("year") or 0)), not bool(item.get("open_access")), str(item.get("title") or "")))
        return sorted(unique.values(), key=lambda item: (not bool(item.get("open_access")), -int(item.get("cited_by_count") or 0), str(item.get("title") or "")))
