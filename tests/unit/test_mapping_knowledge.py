from __future__ import annotations

from copy import deepcopy

import pytest

from geochem.core.database import Database
from geochem.mapping_knowledge import MappingKnowledgeService


@pytest.fixture
def knowledge(tmp_path):
    db = Database(tmp_path / "mapping-knowledge.db")
    db.initialize()
    service = MappingKnowledgeService(db)
    try:
        yield service
    finally:
        db.close()


def _profile(field: str, unit: str = "") -> dict[str, str]:
    return {"field_token": field, "group_context": "whole rock geochemistry", "detected_unit": unit}


def _target(display: str, canonical: str | None = None, unit: str = "") -> dict[str, object]:
    return {
        "display_header": display,
        "canonical_field": canonical or display,
        "target_unit": unit,
        "description": "",
        "order": 0,
    }


@pytest.mark.parametrize(
    ("source", "unit", "target"),
    [
        ("SiO2", "wt%", "SiO2(wt%)"),
        ("Al2O3", "wt%", "Al2O3(wt%)"),
        ("delta13C", "permil", "delta13C"),
        ("87Sr/86Sr", "ratio", "87Sr/86Sr"),
        ("TOC", "wt%", "TOC %"),
        ("Sample No.", "", "SampleID"),
    ],
)
def test_core_geochemical_terms_produce_safe_candidates(knowledge, source, unit, target):
    targets = [_target(target, unit=unit)]

    ranked = knowledge.rank_targets(source, _profile(source, unit), targets)

    assert ranked
    assert ranked[0]["target"]["display_header"] == target
    assert ranked[0]["score"] >= 0.92
    assert ranked[0]["auto_applied"] is True
    assert ranked[0]["release_id"]
    assert ranked[0]["source_references"]


def test_elemental_potassium_never_maps_to_potassium_oxide(knowledge):
    targets = [
        _target("K %", "K", "%"),
        _target("K2O(wt%)", "K2O", "wt%"),
    ]

    ranked = knowledge.rank_targets(
        "relative content of clay minerals(%) K",
        {
            "field_token": "K",
            "group_context": "relative content of clay minerals",
            "detected_unit": "%",
        },
        targets,
    )

    assert [item["target"]["display_header"] for item in ranked] == ["K %"]
    assert ranked[0]["chemical_form"] == "element:K"


def test_iron_oxide_forms_are_not_interchangeable(knowledge):
    targets = [
        _target("FeO", "FeO", "wt%"),
        _target("Fe2O3(wt%)", "Fe2O3", "wt%"),
    ]

    ranked = knowledge.rank_targets("FeO", _profile("FeO", "wt%"), targets)

    assert [item["target"]["display_header"] for item in ranked] == ["FeO"]
    assert ranked[0]["chemical_form"] == "oxide:FeO"


@pytest.mark.parametrize(
    ("source", "source_unit", "target", "canonical", "target_unit"),
    [
        ("K", "%", "K2O(wt%)", "K2O", "wt%"),
        ("FeO", "wt%", "Fe2O3(wt%)", "Fe2O3", "wt%"),
    ],
)
def test_validation_rejects_known_chemical_form_conflicts(
    knowledge,
    source,
    source_unit,
    target,
    canonical,
    target_unit,
):
    validation = knowledge.validate_mapping(
        source,
        _profile(source, source_unit),
        _target(target, canonical, target_unit),
    )

    assert validation["known"] is True
    assert validation["safe"] is False
    assert validation["source_concept_id"] != validation["target_concept_id"]
    assert "化学形态" in validation["reason"]


def test_incompatible_unit_blocks_auto_application(knowledge):
    ranked = knowledge.rank_targets(
        "delta13C",
        _profile("delta13C", "permil"),
        [_target("delta13C ratio", "delta13C", "ratio")],
    )

    assert ranked
    assert ranked[0]["unit_compatibility"] == "incompatible"
    assert ranked[0]["auto_applied"] is False


def test_staged_release_does_not_affect_search_until_published(knowledge, monkeypatch):
    current = knowledge.current_release()
    incoming = deepcopy(knowledge.bundled_snapshot())
    incoming["version"] = "2099.1-test"
    incoming["concepts"].append({
        "concept_id": "test_only_concept",
        "canonical_name": "GeoChemTestOnly",
        "concept_type": "test",
        "chemical_form": "test",
        "unit_dimension": "none",
        "allowed_units": [],
        "contexts": [],
        "forbidden_forms": [],
        "source_references": ["earthchem"],
        "terms": ["GeoChemTestOnly"],
    })
    monkeypatch.setattr(knowledge, "bundled_snapshot", lambda: incoming)

    staged = knowledge.stage_builtin_sync("admin-test")

    assert staged["status"] == "staged"
    assert knowledge.current_release()["release_id"] == current["release_id"]
    assert knowledge.search("GeoChemTestOnly")["items"] == []

    published = knowledge.publish_import(staged["import_id"])

    assert published["release"]["version"] == "2099.1-test"
    assert knowledge.search("GeoChemTestOnly")["items"][0]["concept_id"] == "test_only_concept"
