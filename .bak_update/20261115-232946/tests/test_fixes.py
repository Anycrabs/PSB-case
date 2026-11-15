"""Готовые эмбеддинги в сабмите, ответы без LLM, редакции по дате события, оговорки норм."""

import types

import numpy as np

from legal_core.dates import applicability, extract_event_period
from legal_core.pipeline import answer_question, extractive_answer
from legal_core.prebuilt import attach_vectors, encode_module, load_store, text_key
from legal_core.prompts import NO_CONTEXT_ANSWER, format_context
from legal_core.retrieval import retrieve_context
from legal_core.embeddings import embed_texts
from legal_core.verify import add_missing_caveats, caveat_hints


def _store(texts, matrix, meta=None):
    module = types.ModuleType("lc_vectors")
    exec(encode_module([text_key(t) for t in texts], matrix, meta or {"embedding_model": "m"}), module.__dict__)
    return load_store(module)


def test_prebuilt_roundtrip_and_reuse():
    rng = np.random.default_rng(0)
    matrix = rng.normal(size=(3, 8)).astype(np.float32)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True)
    store = _store(["a", "b", "c"], matrix)
    assert np.allclose(store["matrix"], matrix, atol=2e-3)
    chunks = [{"embed_text": t} for t in ("a", "b", "c", "new")]
    by_text = dict(zip("abc", matrix))
    api = lambda texts: {"dense": np.stack([by_text[t] for t in texts])}  # noqa: E731 — та же модель
    asked = []

    def missing(texts):
        asked.extend(texts)
        return np.ones((len(texts), 8), dtype=np.float32) / np.sqrt(8), len(texts)

    dense, note = attach_vectors(chunks, store, api, missing, lambda _: None)
    assert asked == ["new"], "досчитываются только недостающие фрагменты"
    assert "совпадают" in note and dense.shape == (4, 8)


def test_prebuilt_from_other_model_is_recomputed():
    matrix = np.eye(4, dtype=np.float32)[:2]
    store = _store(["a", "b"], matrix)
    other = lambda texts: {"dense": np.tile([0, 0, 1, 0], (len(texts), 1)).astype(np.float32)}  # noqa: E731
    asked = []

    def missing(texts):
        asked.extend(texts)
        return np.tile([0, 0, 1, 0], (len(texts), 1)).astype(np.float32), len(texts)

    attach_vectors([{"embed_text": "a"}, {"embed_text": "b"}], store, other, missing, lambda _: None)
    assert asked == ["a", "b"]


def test_event_period_from_question():
    period = extract_event_period("Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли одобрение?")
    assert period == {"start": "2024-08-01", "end": "2024-08-31", "label": "август 2024 г."}
    assert extract_event_period("Подписан 15.08.2024")["start"] == "2024-08-15"
    assert extract_event_period("Что требует письмо от 15.04.2025?") is None
    assert extract_event_period("Договор П-17/2024 — гарантия?") is None
    old = {"valid_from": "2023-01-10", "valid_to": "2025-03-01"}
    new = {"valid_from": "2025-03-01", "valid_to": None}
    assert applicability(old, period) == "yes" and applicability(new, period) == "no"
    assert applicability(new, extract_event_period("в 2025 году")) == "partly"


def test_old_edition_gets_into_context(kb, settings):
    _, index = kb
    question = "Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было одобрение генерального директора?"
    period = extract_event_period(question)
    context = retrieve_context(index, [question], lambda t: embed_texts(t, settings), settings, {"event_period": period})
    editions = {c["valid_from"] for c in context if c["doc_title"].startswith("Регламент согласования")}
    assert "2023-01-10" in editions
    text = format_context(context, period)
    assert "ПРИМЕНЯЕТСЯ к событию (август 2024 г.)" in text and "НЕ ПРИМЕНЯЕТСЯ" in text


def test_lost_caveat_is_hinted_and_restored():
    context = [{"label": "S1", "doc_title": "ТК РФ, ст. 70", "citation": "ст. 70",
                "text": "Срок испытания не может превышать трех месяцев, а для главных бухгалтеров и их заместителей "
                        "- шести месяцев, если иное не установлено федеральным законом."}]
    answer = "Главному бухгалтеру срок испытания можно установить не более шести месяцев (ТК РФ, ст. 70) [S1]."
    assert caveat_hints(answer, context)
    fixed = add_missing_caveats(answer, context)
    assert "если иное не установлено федеральным законом" in fixed
    assert add_missing_caveats(fixed, context) == fixed


def test_llm_outage_gives_document_text_not_stub(settings, index):
    def down(*_):
        raise RuntimeError("LLM недоступна")
    from legal_core.runtime import make_local_deps

    deps = make_local_deps(settings, index, llm_json=down)
    result = answer_question("Какие документы нужно приложить к проекту договора на согласование?", [], deps, settings)
    assert result["status"] == "partial"
    assert result["answer_text"].startswith("По базе знаний:")


def test_extractive_answer_picks_matching_line():
    chunk = {"doc_id": "d", "doc_display": "Договор аренды № А-08/2024 с ООО «Северный Склад»", "citation": "п. 1.1–3.3",
             "text": "1.1. Арендодатель передаёт Арендатору помещение площадью 620 кв. м.\n"
                     "3.1. Арендная плата составляет 450 000 рублей в месяц, включая НДС.\n"}
    text = extractive_answer([chunk], "Какая арендная плата по договору с ООО «Северный Склад»?")
    assert "450 000" in text and "620" not in text
    assert extractive_answer([chunk], "Какой размер пособия по беременности и родам?") == NO_CONTEXT_ANSWER
