"""Persistent background-task execution for local and server profiles.

Development keeps a small in-process executor so a Mac can preview GeoChem
without Docker. Staging and production submit the same named operations to
Celery; task state and progress always remain in the project database.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from typing import Any, Callable
from uuid import uuid4

from .core.project import ProjectManager
from .core.runtime import RuntimeSettings, load_runtime_settings
from .services.execution_context import current_user_id, user_execution_context
from .workbench_service import WorkbenchService
from .workflow import WorkflowRunner


ProgressCallback = Callable[[str, float, dict[str, Any] | None], None]


def _now() -> str:
    return datetime.now().isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)


def _task_key(project_id: str, article_id: str | None, task_type: str) -> str:
    raw = f"{project_id}|{article_id or ''}|{task_type}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class WorkflowTaskStore:
    """Database-backed status and event log shared by API and workers."""

    def __init__(self, project_manager: ProjectManager):
        self.pm = project_manager

    def create_or_active(
        self,
        project_id: str,
        article_id: str | None,
        task_type: str,
        operation_name: str,
        payload: dict[str, Any],
        *,
        actor_user_id: str = "",
        max_active_tasks: int = 0,
    ) -> tuple[str, bool]:
        task_key = _task_key(project_id, article_id, task_type)
        db = self.pm.get_database(project_id)
        try:
            active = db.fetch_one(
                """SELECT task_id FROM workflow_tasks
                   WHERE project_id = ?
                     AND COALESCE(article_id, '') = COALESCE(?, '')
                     AND task_type = ? AND status IN ('pending', 'running')
                   ORDER BY created_at DESC LIMIT 1""",
                (project_id, article_id, task_type),
            )
            if active:
                return str(active["task_id"]), False
            if actor_user_id and max_active_tasks > 0:
                task_count = int((db.fetch_one(
                    """SELECT COUNT(*) AS count FROM workflow_tasks
                       WHERE created_by_user_id=? AND status IN ('pending','running')""",
                    (actor_user_id,),
                ) or {"count": 0})["count"])
                run_count = int((db.fetch_one(
                    """SELECT COUNT(*) AS count FROM agent_runs
                       WHERE created_by_user_id=?
                         AND status IN ('pending','running','waiting_user','waiting_workbench')""",
                    (actor_user_id,),
                ) or {"count": 0})["count"])
                if task_count + run_count >= max_active_tasks:
                    raise ValueError(
                        f"当前账号已有 {task_count + run_count} 个进行中的任务，"
                        f"并发上限为 {max_active_tasks}。请等待任务完成或先取消旧任务。"
                    )
            task_id = f"TASK_{uuid4().hex.upper()}"
            try:
                db.execute(
                    """INSERT INTO workflow_tasks
                       (task_id, project_id, created_by_user_id, article_id, task_type, status, progress,
                        message, operation_name, payload_json, task_key, created_at)
                       VALUES (?, ?, ?, ?, ?, 'pending', 0, '等待执行', ?, ?, ?, ?)""",
                    (
                        task_id,
                        project_id,
                        actor_user_id,
                        article_id,
                        task_type,
                        operation_name,
                        _json(payload),
                        task_key,
                        _now(),
                    ),
                )
                db.commit()
                return task_id, True
            except Exception:
                db.rollback()
                active = db.fetch_one(
                    """SELECT task_id FROM workflow_tasks
                       WHERE project_id = ?
                         AND COALESCE(article_id, '') = COALESCE(?, '')
                         AND task_type = ? AND status IN ('pending', 'running')
                       ORDER BY created_at DESC LIMIT 1""",
                    (project_id, article_id, task_type),
                )
                if active:
                    return str(active["task_id"]), False
                raise
        finally:
            db.close()

    def set_broker_task_id(self, project_id: str, task_id: str, broker_task_id: str) -> None:
        db = self.pm.get_database(project_id)
        try:
            db.execute(
                "UPDATE workflow_tasks SET broker_task_id = ? WHERE task_id = ?",
                (broker_task_id, task_id),
            )
            db.commit()
        finally:
            db.close()

    def update(
        self,
        project_id: str,
        task_id: str,
        status: str,
        progress: float,
        message: str,
        details: dict[str, Any] | None = None,
        result: Any = None,
        error: str = "",
    ) -> None:
        db = self.pm.get_database(project_id)
        try:
            now = _now()
            db.execute(
                """UPDATE workflow_tasks SET status = ?, progress = ?, message = ?,
                   result_json = ?, error_message = ?, started_at = COALESCE(started_at, ?),
                   finished_at = CASE WHEN ? IN ('completed','failed') THEN ? ELSE finished_at END
                   WHERE task_id = ?""",
                (
                    status,
                    max(0.0, min(1.0, progress)),
                    message,
                    _json(result),
                    error,
                    now,
                    status,
                    now,
                    task_id,
                ),
            )
            db.execute(
                """INSERT INTO workflow_task_events
                   (task_id, level, message, progress, details_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    "ERROR" if status == "failed" else "INFO",
                    message,
                    progress,
                    _json(details),
                    now,
                ),
            )
            db.commit()
        finally:
            db.close()


def execute_operation(
    project_manager: ProjectManager,
    operation_name: str,
    project_id: str,
    article_id: str | None,
    payload: dict[str, Any],
    progress: ProgressCallback,
) -> Any:
    """Execute one allow-listed, JSON-serializable background operation."""

    article = article_id or str(payload.get("article_id") or "")
    if operation_name == "confirm_access":
        return WorkflowRunner(project_manager=project_manager).confirm_browser_access(
            project_id, article, str(payload.get("url") or "")
        )

    service = WorkbenchService(project_manager)
    if operation_name == "resource_discovery":
        return service.discover_article(project_id, article, progress)
    if operation_name == "header_mapping_benchmark":
        return service.header_mapping_benchmark(project_id, progress)
    if operation_name == "table_standardization":
        return service.standardize_tables(
            project_id, article, bool(payload.get("use_llm", False)), progress
        )
    if operation_name == "candidate_extraction":
        return service.create_extraction_batch(
            project_id, article, bool(payload.get("use_llm", True)), progress
        )
    if operation_name == "table_extraction":
        return service.extract_tables(
            project_id,
            article,
            bool(payload.get("use_llm", False)),
            progress,
            payload.get("element_ids"),
        )
    if operation_name == "paragraph_extraction":
        return service.extract_paragraphs(
            project_id,
            article,
            payload.get("element_ids"),
            bool(payload.get("use_llm", True)),
            progress,
            payload.get("table_element_ids"),
        )
    if operation_name == "element_reextract":
        return service.reextract_element(
            project_id,
            article,
            str(payload.get("element_id") or ""),
            bool(payload.get("use_llm", True)),
            progress,
        )
    raise ValueError(f"Unsupported background operation: {operation_name}")


def execute_persisted_task(
    project_id: str,
    task_id: str,
    operation_name: str,
    article_id: str | None,
    payload: dict[str, Any],
    *,
    project_manager: ProjectManager | None = None,
) -> Any:
    """Run an operation and persist all lifecycle state."""

    runtime = load_runtime_settings()
    pm = project_manager or ProjectManager(base_dir=runtime.storage_root)
    store = WorkflowTaskStore(pm)
    store.update(project_id, task_id, "running", 0.01, "任务已启动")

    def progress(message: str, value: float, details: dict[str, Any] | None = None) -> None:
        store.update(project_id, task_id, "running", value, message, details or {})

    try:
        with user_execution_context(str(payload.get("_actor_user_id") or "")):
            result = execute_operation(
                pm, operation_name, project_id, article_id, payload, progress
            )
        store.update(project_id, task_id, "completed", 1.0, "任务完成", result=result)
        return result
    except Exception as exc:
        store.update(
            project_id,
            task_id,
            "failed",
            1.0,
            f"任务失败: {exc}",
            error=str(exc),
        )
        raise


def create_celery_app(runtime: RuntimeSettings | None = None):
    """Create the worker app lazily so local preview has no Redis requirement."""

    try:
        from celery import Celery
    except ImportError as exc:  # pragma: no cover - exercised by server packaging
        raise RuntimeError("Celery is required when GEOCHEM_TASK_BACKEND=celery") from exc
    settings = runtime or load_runtime_settings()
    app = Celery(
        "geochem",
        broker=settings.effective_celery_broker_url,
        backend=settings.effective_celery_result_backend,
    )
    hard_limit = max(600, min(43200, int(os.environ.get("GEOCHEM_TASK_TIME_LIMIT_SECONDS", "7200"))))
    soft_limit = max(
        300,
        min(hard_limit - 60, int(os.environ.get("GEOCHEM_TASK_SOFT_TIME_LIMIT_SECONDS", "6900"))),
    )
    visibility_timeout = max(
        hard_limit + 300,
        min(86400, int(os.environ.get("GEOCHEM_TASK_VISIBILITY_TIMEOUT_SECONDS", "7500"))),
    )
    result_expires = max(
        3600,
        min(604800, int(os.environ.get("GEOCHEM_TASK_RESULT_EXPIRES_SECONDS", "86400"))),
    )
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone=os.environ.get("TZ", "Asia/Shanghai"),
        enable_utc=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_cancel_long_running_tasks_on_connection_loss=True,
        task_track_started=True,
        task_time_limit=hard_limit,
        task_soft_time_limit=soft_limit,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": visibility_timeout},
        result_backend_transport_options={"visibility_timeout": visibility_timeout},
        visibility_timeout=visibility_timeout,
        result_expires=result_expires,
    )
    return app


try:  # The development environment intentionally does not require Celery.
    celery_app = create_celery_app()
except (ImportError, RuntimeError):  # pragma: no cover - depends on optional package
    celery_app = None


if celery_app is not None:

    @celery_app.task(name="geochem.health.probe")
    def celery_health_probe(nonce: str) -> dict[str, Any]:
        """Prove that a task can cross the broker and return through the backend.

        A Celery control ping only proves that a worker process is visible.  The
        production verifier uses this tiny task to exercise serialization,
        Redis queue delivery, worker execution and result retrieval end to end.
        """

        return {
            "status": "ok",
            "nonce": nonce,
            "worker_pid": os.getpid(),
            "completed_at": _now(),
        }

    @celery_app.task(name="geochem.workflow.execute")
    def celery_execute_workflow(
        project_id: str,
        task_id: str,
        operation_name: str,
        article_id: str | None,
        payload: dict[str, Any],
    ) -> Any:
        return execute_persisted_task(
            project_id, task_id, operation_name, article_id, payload
        )

    @celery_app.task(name="geochem.agent.invoke")
    def celery_invoke_agent(
        project_id: str,
        run_id: str,
        invocation: dict[str, Any],
    ) -> None:
        # Imported lazily to avoid a module cycle during FastAPI startup.
        from langgraph.types import Command

        from .agent_service import ArticleCurationAgent

        runtime = load_runtime_settings()
        pm = ProjectManager(base_dir=runtime.storage_root)
        agent = ArticleCurationAgent(pm, runtime_settings=runtime)
        try:
            with user_execution_context(str(invocation.get("actor_user_id") or "")):
                if invocation.get("kind") == "resume":
                    payload: dict[str, Any] | Command = Command(
                        resume=invocation.get("response") or {}
                    )
                else:
                    payload = invocation.get("state") or {}
                agent._invoke(run_id, project_id, payload)
        finally:
            agent.close()


class TaskDispatcher:
    """Submit named operations to local threads or the shared Celery queue."""

    def __init__(self, project_manager: ProjectManager, runtime: RuntimeSettings):
        self.pm = project_manager
        self.runtime = runtime
        self.store = WorkflowTaskStore(project_manager)
        self.pool = (
            ThreadPoolExecutor(max_workers=3, thread_name_prefix="geochem-web")
            if runtime.task_backend == "local"
            else None
        )

    def submit(
        self,
        project_id: str,
        article_id: str | None,
        task_type: str,
        operation_name: str,
        payload: dict[str, Any] | None = None,
    ) -> str:
        data = dict(payload or {})
        actor_user_id = current_user_id()
        if actor_user_id:
            data.setdefault("_actor_user_id", actor_user_id)
        task_id, created = self.store.create_or_active(
            project_id,
            article_id,
            task_type,
            operation_name,
            data,
            actor_user_id=actor_user_id,
            max_active_tasks=self.runtime.max_concurrent_tasks_per_user,
        )
        if not created:
            return task_id
        if self.pool is not None:
            self.pool.submit(
                execute_persisted_task,
                project_id,
                task_id,
                operation_name,
                article_id,
                data,
                project_manager=self.pm,
            )
            return task_id

        app = celery_app or create_celery_app(self.runtime)
        try:
            result = app.send_task(
                "geochem.workflow.execute",
                args=[project_id, task_id, operation_name, article_id, data],
                task_id=task_id,
                queue="geochem",
            )
            self.store.set_broker_task_id(project_id, task_id, str(result.id))
            return task_id
        except Exception as exc:
            self.store.update(
                project_id,
                task_id,
                "failed",
                1.0,
                f"任务提交失败: {exc}",
                error=str(exc),
            )
            raise ValueError(f"无法提交后台任务: {exc}") from exc

    def close(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=False)


def worker_main() -> None:
    """Console entry point for a production Celery worker."""

    app = celery_app or create_celery_app()
    concurrency = os.environ.get("GEOCHEM_WORKER_CONCURRENCY", "2")
    app.worker_main(
        [
            "worker",
            "--loglevel",
            os.environ.get("GEOCHEM_LOG_LEVEL", "INFO"),
            "--queues",
            "geochem,geochem-agent",
            "--concurrency",
            concurrency,
        ]
    )
