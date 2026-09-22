from __future__ import annotations

import json
from pathlib import Path
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from geochem import agent_service as agent_service_module
from geochem.agent_service import ArticleCurationAgent, RetrievalService
from geochem.core.config import AppConfig, ProviderConfig, TaskModelConfig
from geochem.core.project import ProjectManager
from geochem.web.api import create_app


def _project(tmp_path):
    manager = ProjectManager(tmp_path)
    manager.create_project("Agent Test", "AGENT_TEST")
    db = manager.get_database("AGENT_TEST")
    try:
        db.execute(
            "INSERT INTO articles (article_id, project_id, title, status, created_at) VALUES ('ART_1', 'AGENT_TEST', 'Agent paper', 'imported', '2026-01-01T00:00:00')"
        )
        db.execute(
            "INSERT INTO resources (resource_id, article_id, resource_type, file_name, created_at) VALUES ('RES_1', 'ART_1', 'html_page', 'paper.html', '2026-01-01T00:00:00')"
        )
        db.execute(
            """INSERT INTO header_configs (config_id, project_id, name, headers_json, status, created_at, updated_at)
               VALUES ('HDR_1', 'AGENT_TEST', 'Test headers', '[{"字段名":"SampleID"}]', 'active', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
        )
        db.execute(
            """INSERT INTO article_header_assignments (assignment_id, project_id, article_id, config_id, status, created_at)
               VALUES ('HAS_1', 'AGENT_TEST', 'ART_1', 'HDR_1', 'confirmed', '2026-01-01T00:00:00')"""
        )
        db.execute(
            """INSERT INTO document_elements (element_id, project_id, article_id, resource_id, element_type, page_number,
               text_content, context_text, caption, bbox_json, status, created_at, updated_at)
               VALUES ('ELM_1', 'AGENT_TEST', 'ART_1', 'RES_1', 'paragraph', 3, 'TOC values were measured for HDP-B1.',
               'TOC values were measured for HDP-B1.', 'Results', '[0.1,0.1,0.8,0.2]', 'candidate', '2026-01-01T00:00:00', '2026-01-01T00:00:00')"""
        )
        db.commit()
    finally:
        db.close()
    return manager


def test_rag_index_materializes_document_elements(tmp_path):
    manager = _project(tmp_path)
    rag = RetrievalService(manager)
    result = rag.sync_article("AGENT_TEST", "ART_1")
    assert result["elements"] == 1
    matches = rag._search("AGENT_TEST", "ART_1", "TOC HDP-B1")
    assert matches and matches[0]["element_id"] == "ELM_1"


def test_rag_sync_is_idempotent_when_legacy_queries_emit_duplicate_documents(tmp_path):
    manager = _project(tmp_path)
    rag = RetrievalService(manager)
    original = rag._insert_documents

    def duplicate_once(db, docs):
        original(db, [*docs, docs[0]])

    rag._insert_documents = duplicate_once  # type: ignore[method-assign]
    result = rag.sync_article("AGENT_TEST", "ART_1")
    assert result["documents"] == 1
    db = manager.get_database("AGENT_TEST")
    try:
        assert db.fetch_one("SELECT COUNT(*) AS count FROM retrieval_documents")["count"] == 1
        assert db.fetch_one("SELECT COUNT(*) AS count FROM retrieval_fts")["count"] == 1
    finally:
        db.close()


def test_provenance_rag_prefers_standardized_values_and_statistics_are_local(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    db = manager.get_database("AGENT_TEST")
    try:
        now = "2026-01-01T00:00:00"
        db.execute(
            """INSERT INTO standardized_records
               (record_id, article_id, table_id, row_id, data, quality_grade, processed_at)
               VALUES ('STD_1', 'ART_1', 'TBL_1', 'ROW_1', ?, 'A', ?)""",
            ('{"SampleID":"HDP-B1","SiO2(wt%)":"62.4"}', now),
        )
        db.execute(
            """INSERT INTO standardized_cell_provenance
               (provenance_id, record_id, target_header, standardized_value, target_unit,
                original_field, original_value, original_unit, resource_id, resource_name,
                element_id, element_type, page_number, bbox_json, review_status, confidence,
                source_complete, created_at)
               VALUES ('PROV_1', 'STD_1', 'SiO2(wt%)', '62.4', 'wt%', 'SiO2', '62.4', 'wt%',
                       'RES_1', 'paper.html', 'ELM_1', 'table', 3, '[0.1,0.1,0.8,0.2]',
                       'approved', 0.99, 1, ?)""",
            (now,),
        )
        db.commit()
    finally:
        db.close()

    rag = RetrievalService(manager)
    monkeypatch.setattr(
        rag,
        "_llm_answer",
        lambda _project, _article, _question, evidence: {
            "answer": "HDP-B1 的 SiO2(wt%) 为 62.4。",
            "citations": [{"document_id": evidence[0]["document_id"]}],
            "suggested_actions": ["open_trace"],
        },
    )
    answer = rag.answer("AGENT_TEST", "ART_1", "HDP-B1 的 SiO2(wt%) 来自哪里？")
    assert answer["grounded"] is True
    assert "62.4" in answer["answer"]
    assert answer["citations"][0]["record_id"] == "STD_1"
    assert answer["citations"][0]["element_id"] == "ELM_1"

    stats = rag.statistics("AGENT_TEST", "ART_1", "SiO2(wt%)", "summary")
    assert stats["statistics"]["records"] == 1
    assert stats["statistics"]["mean"] == 62.4
    assert stats["scope"]["missing_policy"] == "数值统计排除空值"
    assert stats["citations"]


def test_agent_pauses_and_resumes_at_human_checkpoints(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")
    started = agent.start("AGENT_TEST", thread["thread_id"], "开始处理这篇文章")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["status"] == "waiting_user", run["error_message"]
    assert run["pending_interrupt"]["kind"] == "resource_confirmation"
    reused = agent.start("AGENT_TEST", thread["thread_id"], "再启动一次")
    assert reused["status"] == "existing"
    assert reused["run_id"] == started["run_id"]
    agent.resume("AGENT_TEST", started["run_id"], {"action": "accept_recommended", "element_ids": ["ELM_1"]})
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["status"] == "waiting_user"
    assert run["pending_interrupt"]["kind"] == "mapping_confirmation"


def test_workbench_handoff_returns_to_original_thread_with_diff(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")
    started = agent.start("AGENT_TEST", thread["thread_id"], "开始处理这篇文章")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["status"] == "waiting_user", run["error_message"]
    assert run["pending_interrupt"]["kind"] == "resource_confirmation"

    handoff = agent.handoff("AGENT_TEST", started["run_id"])
    assert handoff["status"] == "waiting_workbench"
    assert handoff["handoff_context"]["workbench_view"] == "resources"
    assert "view=resources" in handoff["workbench_path"]
    assert f"thread_id={thread['thread_id']}" in handoff["workbench_path"]
    assert f"run_id={started['run_id']}" in handoff["workbench_path"]

    db = manager.get_database("AGENT_TEST")
    try:
        db.execute(
            """INSERT INTO learned_extraction_rules
               (rule_id, project_id, article_id, target_field, target_header, rule_type,
                pattern, review_status, scope, enabled, created_at, created_by)
               VALUES ('RULE_WORKBENCH', 'AGENT_TEST', 'ART_1', 'SampleID', 'SampleID',
                       'source_alias', 'sample', 'confirmed', 'article', 1,
                       '2026-01-01T00:00:00', 'user')"""
        )
        db.commit()
    finally:
        db.close()

    returned = agent.resume_from_workbench("AGENT_TEST", started["run_id"])
    assert returned["status"] == "waiting_user"
    assert returned["thread_id"] == thread["thread_id"]
    assert returned["pending_interrupt"]["kind"] == "workbench_return_review"
    assert returned["workbench_diff"]["changed"] is True
    assert returned["workbench_diff"]["rules"]["added_ids"] == ["RULE_WORKBENCH"]
    assert f"thread_id={thread['thread_id']}" in returned["return_path"]

    restored = agent.thread_state("AGENT_TEST", thread["thread_id"])
    assert restored["thread_id"] == thread["thread_id"]
    assert restored["latest_run"]["run_id"] == started["run_id"]
    assert restored["latest_run"]["status"] == "waiting_user"


def test_agent_can_ask_for_an_article_without_rejecting_a_normal_message(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST")
    started = agent.start("AGENT_TEST", thread["thread_id"], "开始数据提取")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["status"] == "waiting_user"
    assert run["pending_interrupt"]["kind"] == "needs_article"


def test_general_chat_does_not_start_article_processing(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    monkeypatch.setattr(agent, "_general_answer", lambda *_args: ("我是 GeoChem 数据整理 Agent。", False))
    thread = agent.create_thread("AGENT_TEST")
    started = agent.start("AGENT_TEST", thread["thread_id"], "你是什么模型？你能做什么？")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"completed", "failed"}:
            break
    assert run["status"] == "completed", run["error_message"]
    messages = agent.thread("AGENT_TEST", thread["thread_id"])["messages"]
    assert any("GeoChem 数据整理 Agent" in message["content"] for message in messages if message["role"] == "assistant")


def test_project_question_lists_real_imported_articles(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST")
    started = agent.start("AGENT_TEST", thread["thread_id"], "你不能够检索到项目里已经导入的文献？")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"completed", "failed"}:
            break
    assert run["status"] == "completed", run["error_message"]
    messages = agent.thread("AGENT_TEST", thread["thread_id"])["messages"]
    assert any("Agent paper" in message["content"] for message in messages if message["role"] == "assistant")
    assistant = next(message for message in reversed(messages) if message["role"] == "assistant")
    assert assistant["ui_payload"]["kind"] == "entity_cards"


def test_chat_can_select_an_imported_article_with_natural_language(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST")
    started = agent.start("AGENT_TEST", thread["thread_id"], "我想选择 Agent 那篇，我要对这篇进行后续处理")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"completed", "failed"}:
            break
    assert run["status"] == "completed", run["error_message"]
    selected = agent.thread("AGENT_TEST", thread["thread_id"])
    assert selected["article_id"] == "ART_1"
    assert selected["selection_context"]["article"]["entity_id"] == "ART_1"


def test_general_chat_uses_the_configured_model_when_a_key_is_available(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)

    config = AppConfig(
        providers=[ProviderConfig(name="test-openai", api_key="test-key", api_format="openai")],
        task_models={"chat_assistant": TaskModelConfig(provider="test-openai", model="test-model")},
    )

    class Response:
        final_content = "这是经模型生成的功能说明。"
        provider = "test-openai"
        model = "test-model"

    calls: list[dict] = []

    class FakeClient(agent_service_module.LLMClient):
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, messages, **kwargs):
            calls.append({"messages": messages, **kwargs})
            return Response()

    monkeypatch.setattr(agent_service_module, "load_config", lambda: config)
    monkeypatch.setattr(agent_service_module, "LLMClient", FakeClient)
    text, used_model = agent._general_answer("AGENT_TEST", "你是什么模型？可以做什么？")
    assert used_model is True
    assert "这是经模型生成的功能说明" in text
    assert "本次回答已调用模型：test-openai / test-model" in text
    # The public task route is now ``chat_agent``. LLMClient keeps backwards
    # compatibility by resolving it to the configured ``chat_assistant`` route.
    assert calls and calls[0]["task_name"] == "chat_agent"


def test_chat_endpoint_requires_api_key_before_starting_agent(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    config = AppConfig(
        providers=[
            ProviderConfig(
                name="missing-provider",
                api_key="${GEOCHEM_TEST_MISSING_API_KEY}",
                api_format="openai",
            )
        ],
        task_models={
            "_default": TaskModelConfig(provider="missing-provider", model="missing-model"),
            "chat_assistant": TaskModelConfig(provider="missing-provider", model="missing-model"),
        },
    )
    monkeypatch.delenv("GEOCHEM_TEST_MISSING_API_KEY", raising=False)
    monkeypatch.setattr(agent_service_module, "load_config", lambda: config)

    app = create_app(manager)
    with TestClient(app) as client:
        thread = client.post(
            "/api/v1/chat/threads",
            json={"project_id": "AGENT_TEST", "scope": "workspace"},
        ).json()
        response = client.post(
            f"/api/v1/chat/threads/{thread['thread_id']}/messages",
            json={
                "project_id": "AGENT_TEST",
                "content": "你是什么模型？可以做什么？",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["configuration_required"] is True
        assert "尚未配置 API Key" in payload["message"]

        saved = client.get(
            f"/api/v1/chat/threads/{thread['thread_id']}",
            params={"project_id": "AGENT_TEST"},
        ).json()
        assistant = next(
            message for message in reversed(saved["messages"])
            if message["role"] == "assistant"
        )
        assert "尚未配置 API Key" in assistant["content"]
        assert assistant["ui_payload"]["kind"] == "model_configuration_required"

        run = client.get(
            f"/api/v1/agent-runs/{payload['run_id']}",
            params={"project_id": "AGENT_TEST"},
        ).json()
        assert run["status"] == "completed"
        assert run["current_node"] == "model_configuration_required"


def test_agent_activity_updates_replace_stale_checkpoint_node(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")
    db = manager.get_database("AGENT_TEST")
    try:
        db.execute(
            """INSERT INTO agent_runs
               (run_id, project_id, article_id, thread_id, status, current_node,
                workflow_step, run_kind, created_at, updated_at)
               VALUES ('RUN_ACTIVITY', 'AGENT_TEST', 'ART_1', ?, 'running',
                       'mapping_confirmation', 'table_mapping', 'curation', ?, ?)""",
            (thread["thread_id"], "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        db.commit()
    finally:
        db.close()

    agent._set_run_activity(
        "AGENT_TEST", "RUN_ACTIVITY", "paragraph_extraction", "data_extraction"
    )

    run = agent.run("AGENT_TEST", "RUN_ACTIVITY")
    assert run["current_node"] == "paragraph_extraction"
    assert run["workflow_step"] == "data_extraction"

    agent._set_run_progress(
        "AGENT_TEST",
        "RUN_ACTIVITY",
        "paragraph_extraction",
        "data_extraction",
        "已处理 7/37 个资源",
        0.24,
        {"completed": 7, "total": 37},
    )
    progressed = agent.run("AGENT_TEST", "RUN_ACTIVITY")
    activity = progressed["state_summary"]["activity"]
    assert activity["message"] == "已处理 7/37 个资源"
    assert activity["details"] == {"completed": 7, "total": 37}


def test_entity_selection_endpoint_only_accepts_project_objects(tmp_path):
    manager = _project(tmp_path)
    app = create_app(manager)
    with TestClient(app) as client:
        created = client.post("/api/v1/chat/threads", json={"project_id": "AGENT_TEST", "scope": "workspace"}).json()
        items = client.get("/api/v1/agent/entities", params={"project_id": "AGENT_TEST", "type": "article", "context_id": created["thread_id"]})
        assert items.status_code == 200
        assert items.json()[0]["entity_id"] == "ART_1"
        selected = client.post(f"/api/v1/chat/threads/{created['thread_id']}/select", json={"project_id": "AGENT_TEST", "entity_type": "article", "entity_id": "ART_1"})
        assert selected.status_code == 200
        assert selected.json()["selection"]["article_id"] == "ART_1"
        assert any("是否开始数据提取" in message["content"] for message in selected.json()["thread"]["messages"])
        rejected = client.post(f"/api/v1/chat/threads/{created['thread_id']}/select", json={"project_id": "AGENT_TEST", "entity_type": "article", "entity_id": "ART_NOT_LOCAL"})
        assert rejected.status_code == 400


def test_general_literature_search_pauses_for_result_selection(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    monkeypatch.setattr(agent.literature, "search", lambda query: {
        "query": query,
        "providers": ["crossref", "openalex"],
        "errors": [],
        "results": [{
            "literature_id": "LIT_1", "title": "A geochemistry paper", "doi": "10.1000/example",
            "authors": ["A. Author"], "year": 2024, "venue": "GeoChemistry", "landing_url": "https://doi.org/10.1000/example",
            "open_access": True,
        }],
    })
    thread = agent.create_thread("AGENT_TEST")
    started = agent.start("AGENT_TEST", thread["thread_id"], "搜索一下地化数据的文章")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["status"] == "waiting_user", run["error_message"]
    assert run["pending_interrupt"]["kind"] == "literature_search_results"
    assert run["pending_interrupt"]["results"][0]["doi"] == "10.1000/example"
    monkeypatch.setattr(agent.sources, "search", lambda _project, source: {
        "candidates": [{"source_id": "SRC_1", "doi": source, "title": "A geochemistry paper", "access_status": "public"}],
    })
    agent.resume("AGENT_TEST", started["run_id"], {"action": "select_literature", "literature_id": "LIT_1"})
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["status"] == "waiting_user", run["error_message"]
    assert run["pending_interrupt"]["kind"] == "source_confirmation"


def test_second_message_reuses_an_active_article_run(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    first_thread = agent.create_thread("AGENT_TEST", "ART_1")
    started = agent.start("AGENT_TEST", first_thread["thread_id"], "开始处理这篇文章")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] == "waiting_user":
            break
    other_thread = agent.create_thread("AGENT_TEST", "ART_1")
    reused = agent.start("AGENT_TEST", other_thread["thread_id"], "继续处理")
    assert reused["status"] == "existing"
    assert reused["run_id"] == started["run_id"]
    assert reused["thread_id"] == first_thread["thread_id"]


def test_waiting_thread_can_be_stopped_and_deleted(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")
    started = agent.start("AGENT_TEST", thread["thread_id"], "开始处理这篇文章")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] == "waiting_user":
            break
    assert run["status"] == "waiting_user"
    deleted = agent.delete_thread("AGENT_TEST", thread["thread_id"], cancel_waiting=True)
    assert deleted["status"] == "deleted"
    with pytest.raises(ValueError, match="对话不存在"):
        agent.thread("AGENT_TEST", thread["thread_id"])


def test_agent_imports_after_public_source_confirmation_then_requests_header(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    monkeypatch.setattr(agent.sources, "search", lambda _project, _source: {
        "candidates": [{"source_id": "SRC_1", "title": "Open article", "doi": "10.1000/example", "access_status": "public"}],
    })
    monkeypatch.setattr(agent.sources, "confirm", lambda _project, _source: {
        "status": "ready", "article_id": "ART_1", "resource_id": "RES_1",
    })
    thread = agent.create_thread("AGENT_TEST")
    started = agent.start("AGENT_TEST", thread["thread_id"], "提取 DOI: 10.1000/example")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["pending_interrupt"]["kind"] == "source_confirmation"
    agent.resume("AGENT_TEST", started["run_id"], {"action": "confirm_source", "source_id": "SRC_1"})
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    # ART_1 already has a confirmed config in this fixture, so the workflow
    # proceeds directly to resource confirmation after the source handoff.
    assert run["pending_interrupt"]["kind"] == "resource_confirmation"
    assert agent.thread("AGENT_TEST", thread["thread_id"])["article_id"] == "ART_1"


def test_public_pdf_download_failure_returns_upload_guidance_not_failed_run(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    monkeypatch.setattr(agent.sources, "search", lambda _project, _source: {
        "candidates": [{
            "source_id": "SRC_FAIL", "title": "Unavailable article", "doi": "10.1000/unavailable",
            "source_url": "https://doi.org/10.1000/unavailable", "access_status": "public",
        }],
    })
    monkeypatch.setattr(agent.sources, "confirm", lambda _project, _source: {
        "status": "needs_upload", "message": "出版社页面返回 403，可能需要登录。",
    })
    thread = agent.create_thread("AGENT_TEST")
    started = agent.start("AGENT_TEST", thread["thread_id"], "提取 DOI: 10.1000/unavailable")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break
    assert run["pending_interrupt"]["kind"] == "source_confirmation"
    agent.resume("AGENT_TEST", started["run_id"], {"action": "confirm_source", "source_id": "SRC_FAIL"})
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"completed", "failed"}:
            break
    assert run["status"] == "completed", run["error_message"]
    messages = agent.thread("AGENT_TEST", thread["thread_id"])["messages"]
    reply = next(message["content"] for message in reversed(messages) if message["role"] == "assistant")
    assert "10.1000/unavailable" in reply
    assert "PDF" in reply


def test_chat_api_creates_article_scoped_thread_and_reports_rag_status(tmp_path):
    manager = _project(tmp_path)
    app = create_app(manager)
    with TestClient(app) as client:
        created = client.post("/api/v1/chat/threads", json={
            "project_id": "AGENT_TEST", "article_id": "ART_1", "title": "Curation chat",
        })
        assert created.status_code == 200
        thread_id = created.json()["thread_id"]

        listing = client.get("/api/v1/chat/threads", params={"project_id": "AGENT_TEST", "article_id": "ART_1"})
        assert listing.status_code == 200
        assert listing.json()[0]["thread_id"] == thread_id

        reindex = client.post("/api/v1/rag/reindex", params={"project_id": "AGENT_TEST", "article_id": "ART_1"})
        assert reindex.status_code == 200
        status = client.get("/api/v1/rag/status", params={"project_id": "AGENT_TEST", "article_id": "ART_1"})
        assert status.status_code == 200
        assert status.json()["counts"]["element"] == 1


def test_chat_api_deletes_completed_thread(tmp_path):
    manager = _project(tmp_path)
    app = create_app(manager)
    with TestClient(app) as client:
        created = client.post("/api/v1/chat/threads", json={"project_id": "AGENT_TEST"})
        thread_id = created.json()["thread_id"]
        deleted = client.delete(f"/api/v1/chat/threads/{thread_id}", params={"project_id": "AGENT_TEST"})
        assert deleted.status_code == 200
        assert deleted.json()["status"] == "deleted"
        assert client.get(f"/api/v1/chat/threads/{thread_id}", params={"project_id": "AGENT_TEST"}).status_code == 400


def test_new_thread_does_not_inherit_another_threads_article(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    selected = agent.create_thread("AGENT_TEST")
    agent.select_chat_entity(
        "AGENT_TEST",
        selected["thread_id"],
        agent_service_module.EntitySelectionInput(entity_type="article", entity_id="ART_1"),
    )

    fresh = agent.create_thread("AGENT_TEST", scope="workspace")

    assert fresh["article_id"] is None
    assert fresh["selection_context"] == {}
    assert agent.thread_state("AGENT_TEST", fresh["thread_id"])["active_article_id"] == ""


def test_only_latest_actionable_selection_card_remains_active(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", scope="workspace")
    db = manager.get_database("AGENT_TEST")
    try:
        first_id = agent._insert_chat_message(
            db,
            thread_id=thread["thread_id"],
            role="assistant",
            content="请选择文章。",
            ui_payload={
                "kind": "entity_cards",
                "entity_type": "article",
                "entities": [{"entity_id": "ART_1", "title": "First result"}],
            },
        )
        second_id = agent._insert_chat_message(
            db,
            thread_id=thread["thread_id"],
            role="assistant",
            content="请选择新的文章。",
            ui_payload={
                "kind": "entity_cards",
                "entity_type": "article",
                "entities": [{"entity_id": "ART_2", "title": "Second result"}],
            },
        )
        db.commit()
    finally:
        db.close()

    refreshed = agent.thread("AGENT_TEST", thread["thread_id"])
    messages = {message["message_id"]: message for message in refreshed["messages"]}
    assert messages[first_id]["action_state"] == "superseded"
    assert messages[first_id]["superseded_by_message_id"] == second_id
    assert messages[second_id]["action_state"] == "pending"
    assert refreshed["latest_actionable_message_id"] == second_id


def test_langchain_agent_uses_audited_native_tool_calling(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", scope="workspace")
    run_id = "RUN_TOOL_TEST"
    db = manager.get_database("AGENT_TEST")
    try:
        db.execute(
            """INSERT INTO agent_runs
               (run_id, project_id, thread_id, status, run_kind, created_at, updated_at)
               VALUES (?, 'AGENT_TEST', ?, 'running', 'conversation', ?, ?)""",
            (run_id, thread["thread_id"], "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        db.commit()
    finally:
        db.close()

    class FakeClient(agent_service_module.LLMClient):
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, messages, **_kwargs):
            if any(message.get("role") == "tool" for message in messages):
                return SimpleNamespace(
                    final_content="当前工作区有 1 篇文章。",
                    tool_calls=[], provider="test-provider", model="test-tool-model",
                    finish_reason="stop", reasoning_present=False,
                    input_tokens=10, output_tokens=8, total_tokens=18,
                )
            return SimpleNamespace(
                final_content="",
                tool_calls=[{
                    "id": "call_articles",
                    "name": "list_project_entities",
                    "args": {"entity_type": "article", "query": ""},
                }],
                provider="test-provider", model="test-tool-model",
                finish_reason="tool_calls", reasoning_present=False,
                input_tokens=10, output_tokens=4, total_tokens=14,
            )

    monkeypatch.setattr(agent_service_module, "LLMClient", FakeClient)
    result = agent.chat_agent.run(
        project_id="AGENT_TEST",
        thread_id=thread["thread_id"],
        run_id=run_id,
        article_id="",
        user_message="查询当前已经导入的文章",
        selection_context={},
    )

    assert result["tool_mode"] == "native_tool_calling"
    assert result["actual_model"] == {"provider": "test-provider", "model": "test-tool-model"}
    assert result["answer"]["ui_payload"]["kind"] == "entity_cards"
    assert result["answer"]["ui_payload"]["entities"][0]["entity_id"] == "ART_1"
    db = manager.get_database("AGENT_TEST")
    try:
        audit = db.fetch_one("SELECT tool_name, status FROM agent_tool_calls WHERE run_id=?", (run_id,))
        assert dict(audit) == {"tool_name": "list_project_entities", "status": "completed"}
    finally:
        db.close()


def test_header_import_request_exposes_real_upload_action_even_when_model_lists_headers(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", scope="workspace")
    run_id = "RUN_HEADER_IMPORT_TEST"
    db = manager.get_database("AGENT_TEST")
    try:
        db.execute(
            """INSERT INTO agent_runs
               (run_id, project_id, thread_id, status, run_kind, created_at, updated_at)
               VALUES (?, 'AGENT_TEST', ?, 'running', 'conversation', ?, ?)""",
            (run_id, thread["thread_id"], "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        db.commit()
    finally:
        db.close()

    class FakeClient(agent_service_module.LLMClient):
        def __init__(self, *_args, **_kwargs):
            pass

        def chat(self, messages, **_kwargs):
            if any(message.get("role") == "tool" for message in messages):
                return SimpleNamespace(
                    final_content="当前项目有一个表头，但不能单独导入。",
                    tool_calls=[], provider="test-provider", model="test-model",
                    finish_reason="stop", reasoning_present=False,
                    input_tokens=10, output_tokens=8, total_tokens=18,
                )
            return SimpleNamespace(
                final_content="",
                tool_calls=[{
                    "id": "call_headers",
                    "name": "list_project_entities",
                    "args": {"entity_type": "header_config", "query": ""},
                }],
                provider="test-provider", model="test-model",
                finish_reason="tool_calls", reasoning_present=False,
                input_tokens=10, output_tokens=4, total_tokens=14,
            )

    monkeypatch.setattr(agent_service_module, "LLMClient", FakeClient)
    result = agent.chat_agent.run(
        project_id="AGENT_TEST",
        thread_id=thread["thread_id"],
        run_id=run_id,
        article_id="",
        user_message="我可以导入新的表头吗？",
        selection_context={},
    )

    assert result["actual_model"] == {"provider": "test-provider", "model": "test-model"}
    assert result["answer"]["ui_payload"]["kind"] == "header_import_action"
    assert "CSV" in result["answer"]["answer"]
    db = manager.get_database("AGENT_TEST")
    try:
        tools = {
            row["tool_name"]
            for row in db.fetch_all("SELECT tool_name FROM agent_tool_calls WHERE run_id=?", (run_id,))
        }
        assert tools == {"list_project_entities", "request_header_config_import"}
    finally:
        db.close()


def test_critical_standardization_requires_approved_cells_and_is_idempotent(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")
    run_id = "RUN_CRITICAL_TEST"
    db = manager.get_database("AGENT_TEST")
    try:
        now = "2026-01-01T00:00:00"
        db.execute(
            """INSERT INTO agent_runs
               (run_id, project_id, article_id, thread_id, status, run_kind, created_at, updated_at)
               VALUES (?, 'AGENT_TEST', 'ART_1', ?, 'completed', 'conversation', ?, ?)""",
            (run_id, thread["thread_id"], now, now),
        )
        db.execute(
            """INSERT INTO workbench_sessions
               (session_id, project_id, article_id, header_config_id, created_at, updated_at)
               VALUES ('SES_1', 'AGENT_TEST', 'ART_1', 'HDR_1', ?, ?)""",
            (now, now),
        )
        db.execute(
            """INSERT INTO extraction_batches
               (batch_id, project_id, article_id, session_id, header_config_id, status, created_at, updated_at)
               VALUES ('BAT_1', 'AGENT_TEST', 'ART_1', 'SES_1', 'HDR_1', 'completed', ?, ?)""",
            (now, now),
        )
        db.execute(
            """INSERT INTO candidate_records
               (candidate_record_id, batch_id, article_id, sample_key, sample_id, row_index, quality_grade, created_at, updated_at)
               VALUES ('CR_1', 'BAT_1', 'ART_1', 'HDP-B1', 'HDP-B1', 0, 'A', ?, ?)""",
            (now, now),
        )
        db.execute(
            """INSERT INTO candidate_cells
               (cell_id, candidate_record_id, target_header, value, original_value, original_field,
                review_status, element_id, evidence_status, created_at, updated_at)
               VALUES ('CELL_1', 'CR_1', 'SampleID', 'HDP-B1', 'HDP-B1', 'Sample',
                       'pending', 'ELM_1', 'complete', ?, ?)""",
            (now, now),
        )
        db.commit()
    finally:
        db.close()

    with pytest.raises(ValueError, match="未通过人工审核"):
        agent.execute_confirmed_action(
            "AGENT_TEST", thread["thread_id"], run_id, "finalize_standardized", {}
        )

    db = manager.get_database("AGENT_TEST")
    try:
        db.execute("UPDATE candidate_cells SET review_status='approved' WHERE cell_id='CELL_1'")
        db.commit()
    finally:
        db.close()
    first = agent.execute_confirmed_action(
        "AGENT_TEST", thread["thread_id"], run_id, "finalize_standardized", {}
    )
    second = agent.execute_confirmed_action(
        "AGENT_TEST", thread["thread_id"], run_id, "finalize_standardized", {}
    )
    assert first["records"] == 1
    assert second["records"] == 1
    db = manager.get_database("AGENT_TEST")
    try:
        assert db.fetch_one("SELECT COUNT(*) AS count FROM standardized_records WHERE article_id='ART_1'")["count"] == 1
        assert db.fetch_one("SELECT COUNT(*) AS count FROM agent_tool_calls WHERE run_id=? AND status='completed'", (run_id,))["count"] == 1
    finally:
        db.close()


def test_chat_upload_selects_main_pdf_without_starting_agent(tmp_path):
    manager = _project(tmp_path)
    app = create_app(manager)
    pdf_path = tmp_path / "main.pdf"
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Synthetic upload fixture for agent chat test.")
    doc.save(pdf_path)
    doc.close()
    with TestClient(app) as client, pdf_path.open("rb") as source:
        created = client.post("/api/v1/chat/threads", json={"project_id": "AGENT_TEST", "scope": "workspace"}).json()
        uploaded = client.post(
            f"/api/v1/chat/threads/{created['thread_id']}/upload",
            params={"project_id": "AGENT_TEST"},
            files={"file": ("main.pdf", source, "application/pdf")},
        )
        assert uploaded.status_code == 200, uploaded.text
        payload = uploaded.json()
        assert payload["status"] == "selected"
        assert payload["file_name"] == "main.pdf"
        state = client.get(
            f"/api/v1/chat/threads/{created['thread_id']}/state",
            params={"project_id": "AGENT_TEST"},
        ).json()
        assert state["active_article_id"] == payload["article_id"]
        assert state["latest_run"] is None


def test_quality_reextract_routes_back_to_extraction(tmp_path):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)

    assert agent._route_after_quality({
        "confirmations": {"quality": {"action": "reextract"}},
    }) == "reextract"
    assert agent._route_after_quality({
        "confirmations": {"quality": {"action": "open_review"}},
    }) == "finish"


def test_explicit_processing_command_bypasses_chat_tool_routing(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")
    monkeypatch.setattr(
        agent.chat_agent,
        "run",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("chat routing must not run")),
    )

    started = agent.start("AGENT_TEST", thread["thread_id"], "开始数据提取")
    for _ in range(30):
        time.sleep(0.1)
        run = agent.run("AGENT_TEST", started["run_id"])
        if run["status"] in {"waiting_user", "failed"}:
            break

    assert run["status"] == "waiting_user", run["error_message"]
    assert run["pending_interrupt"]["kind"] == "resource_confirmation"
    db = manager.get_database("AGENT_TEST")
    try:
        tool_rows = db.fetch_all(
            """SELECT tool_call_id, tool_name, status
               FROM agent_tool_calls WHERE run_id=? ORDER BY started_at""",
            (started["run_id"],),
        )
        assert [(row["tool_name"], row["status"]) for row in tool_rows] == [
            ("discover_article_resources", "completed"),
            ("sync_article_retrieval", "completed"),
        ]
        events = db.fetch_all(
            "SELECT details_json FROM agent_run_events WHERE run_id=? ORDER BY event_id",
            (started["run_id"],),
        )
        by_call: dict[str, list[str]] = {}
        for row in events:
            details = json.loads(row["details_json"] or "{}")
            if details.get("event_type") != "tool":
                continue
            by_call.setdefault(details["tool_call_id"], []).append(details["status"])
        assert set(by_call) == {row["tool_call_id"] for row in tool_rows}
        assert all(statuses == ["running", "completed"] for statuses in by_call.values())
    finally:
        db.close()


def test_clicked_header_selection_binds_article_and_prompts_continue(tmp_path):
    manager = _project(tmp_path)
    db = manager.get_database("AGENT_TEST")
    try:
        now = "2026-01-02T00:00:00"
        db.execute(
            """INSERT INTO header_configs
               (config_id, project_id, name, headers_json, status, created_at, updated_at)
               VALUES ('HDR_2', 'AGENT_TEST', 'Alternate headers', '[{"字段名":"SampleID"}]',
                       'active', ?, ?)""",
            (now, now),
        )
        db.commit()
    finally:
        db.close()
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")

    result = agent.select_chat_entity(
        "AGENT_TEST",
        thread["thread_id"],
        agent_service_module.EntitySelectionInput(entity_type="header_config", entity_id="HDR_2"),
    )

    assert result["selection"]["header_config"]["entity_id"] == "HDR_2"
    refreshed = agent.thread("AGENT_TEST", thread["thread_id"])
    assert refreshed["messages"][-1]["ui_payload"]["kind"] == "header_selection_confirmation"
    assert "是否继续" in refreshed["messages"][-1]["content"]
    db = manager.get_database("AGENT_TEST")
    try:
        assignment = db.fetch_one(
            """SELECT config_id FROM article_header_assignments
               WHERE project_id='AGENT_TEST' AND article_id='ART_1' AND status='confirmed'"""
        )
        assert assignment["config_id"] == "HDR_2"
    finally:
        db.close()


def test_explicit_empty_resource_selection_does_not_restore_recommendations(tmp_path, monkeypatch):
    manager = _project(tmp_path)
    agent = ArticleCurationAgent(manager)
    thread = agent.create_thread("AGENT_TEST", "ART_1")
    db = manager.get_database("AGENT_TEST")
    try:
        db.execute(
            """INSERT INTO agent_runs
               (run_id, project_id, article_id, thread_id, status, run_kind, created_at, updated_at)
               VALUES ('RUN_TEST', 'AGENT_TEST', 'ART_1', ?, 'running', 'article_curation', ?, ?)""",
            (thread["thread_id"], "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(agent, "_event", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "_set_workflow_step", lambda *_args, **_kwargs: None)

    result = agent._apply_resources({
        "project_id": "AGENT_TEST",
        "article_id": "ART_1",
        "run_id": "RUN_TEST",
        "thread_id": thread["thread_id"],
        "confirmations": {
            "resources": {"action": "custom_selection", "element_ids": []},
        },
    })

    assert result["selected_element_ids"] == []
    assert result["resource_selection_summary"]["selected_count"] == 0
    elements = agent.workbench.list_elements("AGENT_TEST", "ART_1")
    assert not any(element.get("selected") for element in elements)
