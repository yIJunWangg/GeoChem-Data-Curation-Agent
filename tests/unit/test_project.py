"""Tests for project management."""

import tempfile
from pathlib import Path

import pytest

from geochem.core.project import ProjectManager
from geochem.core.exceptions import ProjectAlreadyExistsError, ProjectNotFoundError


@pytest.fixture
def tmp_base(tmp_path):
    return tmp_path / "projects"


@pytest.fixture
def pm(tmp_base):
    return ProjectManager(base_dir=tmp_base)


def test_create_project(pm, tmp_base):
    path = pm.create_project("Test Project", "TEST_001", "A test project")
    assert path.exists()
    assert (path / "project.yaml").exists()
    assert (path / "geochem.db").exists()
    assert (path / "schema").is_dir()
    assert (path / "raw").is_dir()
    assert (path / "output").is_dir()


def test_create_project_duplicate(pm):
    pm.create_project("Test", "TEST_001")
    with pytest.raises(ProjectAlreadyExistsError):
        pm.create_project("Test 2", "TEST_001")


def test_load_project(pm):
    pm.create_project("Test Project", "TEST_001")
    config, path = pm.load_project("TEST_001")
    assert config.project_name == "Test Project"
    assert config.project_id == "TEST_001"


def test_load_project_not_found(pm):
    with pytest.raises(ProjectNotFoundError):
        pm.load_project("NONEXISTENT")


def test_list_projects(pm):
    pm.create_project("Project A", "PRJ_A")
    pm.create_project("Project B", "PRJ_B")
    projects = pm.list_projects()
    assert len(projects) == 2
    ids = [p["project_id"] for p in projects]
    assert "PRJ_A" in ids
    assert "PRJ_B" in ids


def test_get_database(pm):
    pm.create_project("Test", "TEST_DB")
    db = pm.get_database("TEST_DB")
    # Should be able to query
    result = db.fetch_one("SELECT COUNT(*) as cnt FROM articles")
    assert result["cnt"] == 0
    db.close()


def test_ensure_default_workspace(pm):
    config, path = pm.ensure_default_workspace()
    assert config.project_id == "DEFAULT_WORKSPACE"
    assert path.exists()
    assert (path / "articles").is_dir()


def test_article_dir_name_uses_title_and_doi(pm, tmp_base):
    path = pm.create_project("Test Project", "TEST_001")
    rel = pm.ensure_article_dir(path, "Provenance of newly discovered Upper Ordovician black rock units", "10.1002/gj.3514")
    assert rel.startswith("articles/provenance_of_newly_discovered_upper")
    assert "10_1002_gj_3514" in rel
    assert (path / rel / "source").is_dir()
    assert (path / rel / "assets" / "tables").is_dir()
