"""Project bootstrap tests that do not depend on a graphical client."""

from concurrent.futures import ThreadPoolExecutor

from geochem.core.project import DEFAULT_WORKSPACE_ID, PROJECT_DIRS, ProjectManager


def test_default_workspace_bootstrap_is_concurrency_safe(tmp_path):
    manager = ProjectManager(base_dir=tmp_path / "projects")

    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(lambda _index: manager.ensure_default_workspace(), range(12)))

    assert {config.project_id for config, _path in results} == {DEFAULT_WORKSPACE_ID}
    workspace_path = manager.base_dir / DEFAULT_WORKSPACE_ID
    assert (workspace_path / "project.yaml").is_file()
    assert all((workspace_path / directory).is_dir() for directory in PROJECT_DIRS)

    db = manager.get_database(DEFAULT_WORKSPACE_ID)
    try:
        row = db.fetch_one(
            "SELECT COUNT(*) AS total FROM projects WHERE project_id = ?",
            (DEFAULT_WORKSPACE_ID,),
        )
        assert row["total"] == 1
    finally:
        db.close()
