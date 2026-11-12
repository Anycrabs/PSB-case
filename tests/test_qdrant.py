from legal_core.embeddings import embed_texts
from legal_core.index import qdrant_client, qdrant_search, qdrant_upload
from legal_core.retrieval import hybrid_search


def test_qdrant_memory_hybrid(settings, index):
    client = qdrant_client({**settings, "qdrant_url": ":memory:"})
    qdrant_upload(client, index, "test_chunks")
    assert client.count("test_chunks").count == len(index["chunks"])
    emb = embed_texts(["выписка из ЕГРЮЛ контрагента"], settings)
    hits = qdrant_search(client, "test_chunks", emb["dense"][0], emb["sparse"][0], {}, 5)
    assert hits
    qdrant = {"client": client, "collection": "test_chunks"}
    results = hybrid_search(index, "выписка из ЕГРЮЛ контрагента", lambda t: embed_texts(t, settings), settings,
                            {"event_date": "2026-05-01"}, qdrant)
    assert "ст. 3" in index["chunks"][results[0]["idx"]]["citation"]
