from __future__ import annotations

from datetime import datetime

import json
import pytest

from geochem.background_tasks import TaskDispatcher, WorkflowTaskStore, celery_app, create_celery_app
from geochem.core.runtime import load_runtime_settings
from geochem.core.project import ProjectManager
from geochem.services.execution_context import user_execution_context


def _project(tmp_path):
    pm = ProjectManager(base_dir=tmp_path / "projects")
    project = pm.create_project("Task test", project_id="TASK_TEST")
    return pm, project


def test_task_store_reuses_active_operation(tmp_path):
    pm, _project_config = _project(tmp_path)
    store = WorkflowTaskStore(pm)

    first, created = store.create_or_active(
        "TASK_TEST", "ART_1", "resource_discovery", "resource_discovery", {}
    )
    second, second_created = store.create_or_active(
        "TASK_TEST", "ART_1", "resource_discovery", "resource_discovery", {}
    )

    assert created is True
    assert second_created is False
    assert second == first
    assert first.startswith("TASK_")


def test_task_store_enforces_per_user_concurrency_limit(tmp_path):
    pm, _project_config = _project(tmp_path)
    store = WorkflowTaskStore(pm)

    store.create_or_active(
        "TASK_TEST",
        "ART_1",
        "resource_discovery",
        "resource_discovery",
        {},
        actor_user_id="curator-actor",
        max_active_tasks=1,
    )

    with pytest.raises(ValueError, match="并发上限为 1"):
        store.create_or_active(
            "TASK_TEST",
            "ART_2",
            "table_extraction",
            "table_extraction",
            {},
            actor_user_id="curator-actor",
            max_active_tasks=1,
        )


def test_task_store_recognizes_legacy_active_task_without_key(tmp_path):
    pm, _project_config = _project(tmp_path)
    db = pm.get_database("TASK_TEST")
    try:
        db.execute(
            """INSERT INTO workflow_tasks
               (task_id, project_id, article_id, task_type, status, progress, message, created_at)
               VALUES ('TASK_LEGACY', 'TASK_TEST', 'ART_1', 'table_extraction',
                       'running', 0.5, 'legacy', ?)""",
            (datetime.now().isoformat(),),
        )
        db.commit()
    finally:
        db.close()

    task_id, created = WorkflowTaskStore(pm).create_or_active(
        "TASK_TEST", "ART_1", "table_extraction", "table_extraction", {}
    )

    assert created is False
    assert task_id == "TASK_LEGACY"


def test_celery_long_task_delivery_settings(monkeypatch):
    monkeypatch.setenv("GEOCHEM_TASK_TIME_LIMIT_SECONDS", "7200")
    monkeypatch.setenv("GEOCHEM_TASK_SOFT_TIME_LIMIT_SECONDS", "6900")
    monkeypatch.setenv("GEOCHEM_TASK_VISIBILITY_TIMEOUT_SECONDS", "7500")
    monkeypatch.setenv("GEOCHEM_TASK_RESULT_EXPIRES_SECONDS", "86400")

    app = create_celery_app(load_runtime_settings({}))

    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True
    assert app.conf.worker_prefetch_multiplier == 1
    assert app.conf.broker_connection_retry_on_startup is True
    assert app.conf.task_time_limit == 7200
    assert app.conf.task_soft_time_limit == 6900
    assert app.conf.broker_transport_options["visibility_timeout"] == 7500
    assert app.conf.result_backend_transport_options["visibility_timeout"] == 7500
    assert app.conf.result_expires == 86400


def test_celery_health_probe_returns_a_correlated_payload():
    assert celery_app is not None
    probe = celery_app.tasks["geochem.health.probe"]

    payload = probe.run("acceptance-nonce")

    assert payload["status"] == "ok"
    assert payload["nonce"] == "acceptance-nonce"
    assert isinstance(payload["worker_pid"], int)
    assert payload["completed_at"]


def test_task_dispatcher_persists_authenticated_actor_for_worker_context(tmp_path):
    pm, _project_config = _project(tmp_path)
    dispatcher = TaskDispatcher(pm, load_runtime_settings({}))
    assert dispatcher.pool is not None
    dispatcher.pool.shutdown(wait=True)

    class CapturePool:
        def submit(self, *args, **kwargs):
            return None

    dispatcher.pool = CapturePool()
    with user_execution_context("curator-actor"):
        task_id = dispatcher.submit(
            "TASK_TEST",
            "ART_1",
            "resource_discovery",
            "resource_discovery",
            {},
        )

    db = pm.get_database("TASK_TEST")
    try:
        row = db.fetch_one(
            "SELECT payload_json FROM workflow_tasks WHERE task_id=?",
            (task_id,),
        )
        assert json.loads(row["payload_json"])["_actor_user_id"] == "curator-actor"
    finally:
        db.close()
