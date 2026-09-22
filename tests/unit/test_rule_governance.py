from __future__ import annotations

from pathlib import Path

import pytest

from geochem.core.project import DEFAULT_WORKSPACE_ID, ProjectManager
from geochem.services.rule_governance import RuleGovernanceService
from geochem.services.workspace_access import WorkspaceAccessService
from geochem.workbench_service import WorkbenchService


@pytest.fixture
def rule_services(tmp_path):
    manager = ProjectManager(tmp_path / "workspaces")
    WorkspaceAccessService(manager).bootstrap_default_organization()
    return manager, RuleGovernanceService(manager), WorkbenchService(manager)


def _safe_sample_rule(**overrides):
    payload = {
        "source_term": "Sample No.",
        "target_canonical_field": "SampleID",
        "target_header": "SampleID",
        "source_unit": "",
        "target_unit": "",
        "chemical_form": "identifier",
        "context": "sample table identifier column",
        "evidence": "Organization-confirmed sample identifier convention",
        "scope": "organization",
        "confidence": 0.98,
    }
    payload.update(overrides)
    return payload


def _publish(service: RuleGovernanceService, payload: dict, *, creator: str = "curator-user"):
    draft = service.create_submission(
        DEFAULT_WORKSPACE_ID,
        creator,
        "curator",
        (),
        payload,
    )
    pending = service.submit(DEFAULT_WORKSPACE_ID, draft["submission_id"], creator)
    published = service.approve(
        DEFAULT_WORKSPACE_ID,
        pending["submission_id"],
        "owner-user",
        "owner",
        (),
        "Approved for organization reuse",
    )
    return draft, pending, published


def test_member_submission_requires_owner_approval_before_runtime(rule_services):
    _manager, governance, workbench = rule_services

    with pytest.raises(PermissionError, match="只能查看"):
        governance.create_submission(
            DEFAULT_WORKSPACE_ID,
            "viewer-user",
            "viewer",
            (),
            _safe_sample_rule(),
        )

    draft = governance.create_submission(
        DEFAULT_WORKSPACE_ID,
        "curator-user",
        "curator",
        (),
        _safe_sample_rule(),
    )
    assert draft["status"] == "draft"
    assert governance.list_rules(DEFAULT_WORKSPACE_ID)["items"] == []
    assert workbench.rule_memory(DEFAULT_WORKSPACE_ID, "ART_TEST")["extraction"] == []

    pending = governance.submit(DEFAULT_WORKSPACE_ID, draft["submission_id"], "curator-user")
    assert pending["status"] == "pending"
    assert workbench.rule_memory(DEFAULT_WORKSPACE_ID, "ART_TEST")["extraction"] == []
    with pytest.raises(PermissionError, match="Owner"):
        governance.approve(
            DEFAULT_WORKSPACE_ID,
            pending["submission_id"],
            "curator-user",
            "curator",
            (),
        )

    result = governance.approve(
        DEFAULT_WORKSPACE_ID,
        pending["submission_id"],
        "owner-user",
        "owner",
        (),
        "Safe organization alias",
    )

    rule = result["rule"]
    assert rule["scope"] == "organization"
    assert rule["review_status"] == "confirmed"
    assert rule["published_by"] == "owner-user"
    assert rule["conditions"]["auto_apply_allowed"] is True
    runtime_rules = workbench.rule_memory(DEFAULT_WORKSPACE_ID, "ART_TEST")["extraction"]
    assert [item["rule_id"] for item in runtime_rules] == [rule["rule_id"]]


def test_rejected_rule_never_enters_runtime(rule_services):
    _manager, governance, workbench = rule_services
    draft = governance.create_submission(
        DEFAULT_WORKSPACE_ID,
        "reviewer-user",
        "reviewer",
        (),
        _safe_sample_rule(source_term="Specimen ID"),
    )
    governance.submit(DEFAULT_WORKSPACE_ID, draft["submission_id"], "reviewer-user")
    rejected = governance.reject(
        DEFAULT_WORKSPACE_ID,
        draft["submission_id"],
        "owner-user",
        "owner",
        (),
        "The organization uses a different specimen convention.",
    )

    assert rejected["status"] == "rejected"
    assert rejected["reviews"][0]["decision"] == "rejected"
    assert workbench.rule_memory(DEFAULT_WORKSPACE_ID, "ART_TEST")["extraction"] == []


def test_published_rule_revision_preserves_history(rule_services):
    _manager, governance, workbench = rule_services
    _draft, _pending, first = _publish(governance, _safe_sample_rule())
    first_rule = first["rule"]

    _, _, second = _publish(
        governance,
        _safe_sample_rule(
            notes="Second reviewed wording",
            supersedes_rule_id=first_rule["rule_id"],
        ),
        creator="reviewer-user",
    )
    second_rule = second["rule"]

    assert second_rule["revision"] == 2
    assert second_rule["supersedes_rule_id"] == first_rule["rule_id"]
    history = governance.rule_history(DEFAULT_WORKSPACE_ID, second_rule["rule_id"])
    assert [item["revision"] for item in history] == [2, 1]
    assert history[1]["review_status"] == "superseded"
    assert history[1]["enabled"] is False
    active = workbench.rule_memory(DEFAULT_WORKSPACE_ID, "ART_TEST")["extraction"]
    assert [item["rule_id"] for item in active] == [second_rule["rule_id"]]


def test_rule_import_previews_duplicates_and_chemical_conflicts(rule_services, tmp_path):
    _manager, governance, workbench = rule_services
    path = tmp_path / "mapping-rules.csv"
    path.write_text(
        "source_term,target_canonical_field,source_unit,target_unit,chemical_form,context,conversion_formula,evidence,notes\n"
        "Sample No.,SampleID,,,identifier,sample table,,organization convention,valid\n"
        "Sample No.,SampleID,,,identifier,sample table,,duplicate,duplicate\n"
        "K,K2O,wt%,wt%,element:K,whole rock,,unsafe conversion,conflict\n",
        encoding="utf-8",
    )

    preview = governance.import_file(
        DEFAULT_WORKSPACE_ID,
        "curator-user",
        "curator",
        (),
        Path(path),
        path.name,
    )

    assert preview["total_rows"] == 3
    assert preview["valid_rows"] == 1
    assert preview["invalid_rows"] == 2
    assert len(preview["submission_ids"]) == 1
    assert any(item["status"] == "duplicate_in_file" for item in preview["preview"])
    assert any(
        "化学形态" in message
        for item in preview["preview"]
        for message in item.get("validation", {}).get("errors", [])
    )
    assert workbench.rule_memory(DEFAULT_WORKSPACE_ID, "ART_TEST")["extraction"] == []


def test_rule_revision_cannot_supersede_an_unrelated_rule(rule_services):
    _manager, governance, _workbench = rule_services
    _, _, sample_result = _publish(governance, _safe_sample_rule())
    _, _, toc_result = _publish(
        governance,
        _safe_sample_rule(
            source_term="Total organic carbon",
            target_canonical_field="TOC",
            target_header="TOC",
            chemical_form="total_organic_carbon",
            context="bulk geochemistry",
        ),
        creator="reviewer-user",
    )

    draft = governance.create_submission(
        DEFAULT_WORKSPACE_ID,
        "curator-user",
        "curator",
        (),
        _safe_sample_rule(
            notes="Attempt to replace an unrelated concept",
            supersedes_rule_id=toc_result["rule"]["rule_id"],
        ),
    )
    governance.submit(DEFAULT_WORKSPACE_ID, draft["submission_id"], "curator-user")

    with pytest.raises(ValueError, match="同一映射冲突"):
        governance.approve(
            DEFAULT_WORKSPACE_ID,
            draft["submission_id"],
            "owner-user",
            "owner",
            (),
        )

    assert governance.rule(
        DEFAULT_WORKSPACE_ID,
        sample_result["rule"]["rule_id"],
    )["review_status"] == "confirmed"
    assert governance.rule(
        DEFAULT_WORKSPACE_ID,
        toc_result["rule"]["rule_id"],
    )["review_status"] == "confirmed"


def test_rule_import_preview_is_private_until_owner_review(rule_services, tmp_path):
    _manager, governance, _workbench = rule_services
    path = tmp_path / "private-rules.csv"
    path.write_text(
        "source_term,target_canonical_field,source_unit,target_unit,chemical_form,context,conversion_formula,evidence,notes\n"
        "Sample name,SampleID,,,identifier,sample table,,organization convention,valid\n",
        encoding="utf-8",
    )
    preview = governance.import_file(
        DEFAULT_WORKSPACE_ID,
        "curator-user",
        "curator",
        (),
        path,
        path.name,
    )

    with pytest.raises(PermissionError, match="自己上传"):
        governance.import_preview(
            DEFAULT_WORKSPACE_ID,
            preview["import_id"],
            actor_id="reviewer-user",
        )

    owner_preview = governance.import_preview(
        DEFAULT_WORKSPACE_ID,
        preview["import_id"],
        actor_id="owner-user",
        can_view_all=True,
    )
    assert owner_preview["created_by"] == "curator-user"
