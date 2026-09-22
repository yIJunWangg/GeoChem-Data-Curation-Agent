from __future__ import annotations

from geochem.agent_service import RetrievalService
from geochem.core.project import ProjectManager
from geochem.core.runtime import RuntimeSettings
from geochem.semantic_retrieval import EmbeddingProvider, EvidenceChunker


class FakeEmbedding(EmbeddingProvider):
    dimension = 3
    model_version = "fake-v1"

    def __init__(self):
        self.document_calls = 0

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_calls += 1
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return [float(len(text)), 1.0, 0.0]


class FakeVectorStore:
    def __init__(self):
        self.payloads = {}
        self.deleted: list[str] = []

    @staticmethod
    def point_id(chunk_id: str) -> str:
        return f"point:{chunk_id}"

    def ready(self) -> bool:
        return True

    def upsert(self, chunks, vectors):
        for chunk, vector in zip(chunks, vectors, strict=True):
            self.payloads[chunk.chunk_id] = {"chunk": chunk, "vector": vector}

    def delete(self, chunk_ids):
        self.deleted.extend(chunk_ids)
        for chunk_id in chunk_ids:
            self.payloads.pop(chunk_id, None)

    def list_chunk_ids(self, *, project_id, article_id):
        return [
            chunk_id for chunk_id, item in self.payloads.items()
            if item["chunk"].project_id == project_id and item["chunk"].article_id == article_id
        ]

    def search(self, _vector, *, project_id, article_id, limit=30):
        return [
            {"score": 0.99, "payload": {"chunk_id": chunk_id}}
            for chunk_id, item in self.payloads.items()
            if item["chunk"].project_id == project_id and item["chunk"].article_id == article_id
        ][:limit]


def _project(tmp_path):
    manager = ProjectManager(tmp_path)
    manager.create_project("Semantic Test", "SEMANTIC_TEST")
    db = manager.get_database("SEMANTIC_TEST")
    try:
        db.execute(
            "INSERT INTO articles (article_id, project_id, title, status, created_at) VALUES ('ART_1', 'SEMANTIC_TEST', 'Paper', 'imported', '2026-01-01')"
        )
        db.execute(
            "INSERT INTO resources (resource_id, article_id, resource_type, file_name, created_at) VALUES ('RES_1', 'ART_1', 'pdf', 'paper.pdf', '2026-01-01')"
        )
        db.execute(
            """INSERT INTO document_elements
               (element_id, project_id, article_id, resource_id, element_type, page_number,
                bbox_json, page_spans_json, text_content, context_text, caption,
                raw_table_json, reading_order, section_path, status, created_at, updated_at)
               VALUES ('EL_1', 'SEMANTIC_TEST', 'ART_1', 'RES_1', 'paragraph', 4,
                       '[0.1,0.2,0.8,0.4]', '[{"page_number":4,"bbox":[0.1,0.2,0.8,0.4]}]',
                       'Samples came from a restricted lower Cambrian marine basin.', '', '', '{}',
                       7, 'GEOLOGICAL SETTING', 'candidate', '2026-01-01', '2026-01-01')"""
        )
        db.commit()
    finally:
        db.close()
    return manager


def test_structure_aware_chunking_preserves_provenance_and_overlap():
    chunker = EvidenceChunker(max_tokens=64, overlap_tokens=8)
    text = " ".join(f"token-{index}" for index in range(150))
    chunks = chunker.build([{
        "document_id": "DOC_1", "project_id": "P1", "article_id": "A1",
        "resource_id": "R1", "element_id": "E1", "document_type": "element",
        "content": text,
        "metadata": {
            "element_type": "paragraph", "page_number": 2,
            "page_spans": [{"page_number": 2, "bbox": [0.1, 0.2, 0.8, 0.9]}],
            "bbox": [0.1, 0.2, 0.8, 0.9], "section_path": "METHODS", "reading_order": 3,
        },
    }])

    assert len(chunks) >= 3
    assert all(chunk.section_path == "METHODS" for chunk in chunks)
    assert all(chunk.page_number == 2 for chunk in chunks)
    assert all(chunk.element_id == "E1" for chunk in chunks)
    assert chunks[0].chunk_id != chunks[1].chunk_id


def test_table_chunks_repeat_headers_and_do_not_cross_documents():
    chunker = EvidenceChunker(max_tokens=64, overlap_tokens=8)
    documents = []
    for suffix in ("A", "B"):
        documents.append({
            "document_id": f"DOC_{suffix}", "project_id": "P1", "article_id": "A1",
            "resource_id": "R1", "element_id": f"E_{suffix}", "document_type": "element",
            "content": "table",
            "metadata": {
                "element_type": "table", "caption": f"Table {suffix}",
                "raw_table": {"headers": ["SampleID", "SiO2"], "rows": [[f"{suffix}-{i}", str(i)] for i in range(40)]},
            },
        })
    chunks = chunker.build(documents)

    assert {chunk.parent_document_id for chunk in chunks} == {"DOC_A", "DOC_B"}
    assert all("Headers: SampleID | SiO2" in chunk.content for chunk in chunks)
    assert all(("Table A" in chunk.content) ^ ("Table B" in chunk.content) for chunk in chunks)


def test_vector_sync_is_incremental_and_semantic_search_is_scoped(tmp_path):
    manager = _project(tmp_path)
    embedding = FakeEmbedding()
    vectors = FakeVectorStore()
    runtime = RuntimeSettings(vector_enabled=True, embedding_model="fake", embedding_batch_size=8)
    retrieval = RetrievalService(
        manager,
        runtime,
        embedding_provider=embedding,
        vector_store=vectors,
    )

    first = retrieval.sync_article("SEMANTIC_TEST", "ART_1")
    second = retrieval.sync_article("SEMANTIC_TEST", "ART_1")
    results = retrieval._search("SEMANTIC_TEST", "ART_1", "完全不同的中文语义查询")

    assert first["chunks"] >= 1
    assert first["vector_indexed"] == first["chunks"]
    assert second["vector_reused"] == second["chunks"]
    assert embedding.document_calls == 1
    assert results
    assert results[0]["article_id"] == "ART_1"
    assert results[0]["element_id"] == "EL_1"
    assert "vector" in results[0]["metadata"]["retrieval_methods"]

    old_ids = set(vectors.payloads)
    db = manager.get_database("SEMANTIC_TEST")
    try:
        db.execute(
            "UPDATE document_elements SET text_content='Samples came from an Ordovician carbonate platform.', context_text='', updated_at='2026-01-02' WHERE element_id='EL_1'"
        )
        db.commit()
    finally:
        db.close()
    changed = retrieval.sync_article("SEMANTIC_TEST", "ART_1")

    assert changed["vector_indexed"] >= 1
    assert changed["vector_deleted"] == len(old_ids)
    assert old_ids.isdisjoint(vectors.payloads)


def test_vector_failure_falls_back_to_chunk_fts(tmp_path):
    manager = _project(tmp_path)
    runtime = RuntimeSettings(vector_enabled=False)
    retrieval = RetrievalService(manager, runtime)
    retrieval.sync_article("SEMANTIC_TEST", "ART_1")

    results = retrieval._search("SEMANTIC_TEST", "ART_1", "Cambrian basin")

    assert results
    assert results[0]["element_id"] == "EL_1"
    assert results[0]["metadata"]["retrieval_methods"] == ["bm25"]
