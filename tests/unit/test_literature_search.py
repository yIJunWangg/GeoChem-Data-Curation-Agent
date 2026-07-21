from __future__ import annotations

from geochem.ingestion.literature_search import LiteratureSearchService


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class _Session:
    headers = {}

    def get(self, url, params, timeout):
        if "crossref" in url:
            return _Response({"message": {"items": [{
                "DOI": "10.1000/example", "title": ["Geochemical data from a basin"],
                "author": [{"given": "Ada", "family": "Stone"}], "issued": {"date-parts": [[2024]]},
                "container-title": ["Geochemistry Journal"], "URL": "https://doi.org/10.1000/example",
            }]}})
        return _Response({"results": [{
            "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/example",
            "title": "Geochemical data from a basin", "publication_year": 2024,
            "authorships": [{"author": {"display_name": "Ada Stone"}}],
            "primary_location": {"landing_page_url": "https://doi.org/10.1000/example", "source": {"display_name": "Geochemistry Journal"}},
            "open_access": {"is_oa": True}, "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
            "cited_by_count": 7,
        }]})


def test_literature_search_merges_public_index_results_by_doi():
    result = LiteratureSearchService(_Session()).search("geochemistry")
    assert result["providers"] == ["crossref", "openalex"]
    assert len(result["results"]) == 1
    item = result["results"][0]
    assert item["doi"] == "10.1000/example"
    assert item["pdf_url"] == "https://example.org/paper.pdf"
    assert item["open_access"] is True
