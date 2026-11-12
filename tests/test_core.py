import numpy as np

from legal_core.chunking import chunk_documents
from legal_core.index import allowed_mask, load_index, save_index, search_dense
from legal_core.lexical import tokenize
from legal_core.llm import parse_json_loose
from legal_core.retrieval import extract_references, hybrid_search, retrieve_context, rrf_fuse
from legal_core.embeddings import embed_texts


def test_chunks_have_citations_and_spans(docs):
    chunks = chunk_documents(docs)
    reg = [c for c in chunks if c["doc_type"] == "regulation"]
    citations = {c["citation"] for c in reg}
    assert any(c.startswith("ст. 3") for c in citations) and any(c.startswith("ст. 5") for c in citations)
    assert any(c["is_definition"] for c in reg if c["citation"].startswith("ст. 1"))
    doc_text = {d["doc_id"]: d["text"] for d in docs}
    norm = lambda t: " ".join(t.split())  # noqa: E731
    for c in chunks:
        a, b = c["char_span"]
        assert norm(doc_text[c["doc_id"]][a:b])[:40] == norm(c["text"])[:40]


def test_doc_types(docs):
    types = {d["source_file"]: d["doc_type"] for d in docs}
    assert types["reglament_soglasovaniya.txt"] == "regulation"
    assert types["dogovor_17.txt"] == "contract"
    assert types["ds_1_k_dogovoru_17.txt"] == "supplement"


def test_references_and_rrf():
    refs = extract_references("Что сказано в статье 5 и в п. 2.2 договора?")
    assert refs == {"articles": ["5"], "points": ["2.2"]}
    fused = rrf_fuse([[(1, 0.9), (2, 0.8)], [(2, 5.0), (3, 4.0)]])
    assert fused[0][0] == 2


def test_hybrid_search_finds_relevant(settings, index):
    embed = lambda texts: embed_texts(texts, settings)  # noqa: E731
    hits = hybrid_search(index, "какие документы приложить к проекту договора выписка ЕГРЮЛ", embed, settings)
    top = [index["chunks"][h["idx"]]["text"] for h in hits[:3]]
    assert any("ЕГРЮЛ" in t for t in top)
    exact = hybrid_search(index, "Что предусматривает статья 5?", embed, settings)
    assert index["chunks"][exact[0]["idx"]]["citation"].startswith("ст. 5")
    assert "exact" in exact[0]["channels"]


def test_context_labels(settings, index):
    embed = lambda texts: embed_texts(texts, settings)  # noqa: E731
    context = retrieve_context(index, ["срок оплаты услуг по договору"], embed, settings)
    assert [c["label"] for c in context] == [f"S{i}" for i in range(1, len(context) + 1)]
    assert len(context) <= settings["context_chunks"]


def test_save_load_roundtrip(tmp_path, index):
    save_index(index, tmp_path)
    loaded = load_index(tmp_path)
    assert len(loaded["chunks"]) == len(index["chunks"])
    assert np.allclose(loaded["dense"], index["dense"])
    assert loaded["meta"]["has_sparse"] is True


def test_event_date_filter(index):
    chunks = index["chunks"]
    original = [dict(c) for c in chunks]
    chunks[0]["valid_to"], chunks[1]["valid_from"] = "2026-04-01", "2026-04-01"
    try:
        before = allowed_mask(index, {"event_date": "2026-03-15"})
        after = allowed_mask(index, {"event_date": "2026-04-10"})
        current = allowed_mask(index, {})
        assert before[0] and not before[1]
        assert not after[0] and after[1]
        assert all(current)  # без даты обе редакции идут в контекст, модель выбирает по дате из вопроса
    finally:
        for c, o in zip(chunks, original):
            c.clear(), c.update(o)


def test_dense_respects_mask(settings, index):
    vec = embed_texts(["договор"], settings)["dense"][0]
    mask = [c["doc_type"] == "contract" for c in index["chunks"]]
    hits = search_dense(index, vec, mask, 50)
    assert hits and all(index["chunks"][i]["doc_type"] == "contract" for i, _ in hits)


def test_parse_json_loose():
    assert parse_json_loose('<think>…</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_loose('Ответ: {"b": [1, 2]} конец') == {"b": [1, 2]}


def test_tokenize_stems():
    assert tokenize("договорами") == tokenize("договоров")
