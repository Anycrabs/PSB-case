import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from devtools.fake_llm import fake_answer  # noqa: E402
from legal_core.chunking import chunk_documents  # noqa: E402
from legal_core.config import get_settings  # noqa: E402
from legal_core.embeddings import embed_texts  # noqa: E402
from legal_core.index import build_index  # noqa: E402
from legal_core.parsing import read_knowledge_base  # noqa: E402
from legal_core.runtime import make_local_deps  # noqa: E402

KB_ZIP = ROOT / "data" / "knowledge-base.zip"


@pytest.fixture(scope="session")
def settings():
    return {**get_settings(), "embedder_mode": "fake", "reranker_model": "", "pipeline_mode": "auto"}


def _index(settings, path):
    docs, errors = read_knowledge_base(path)
    assert not errors
    chunks = chunk_documents(docs)
    return docs, build_index(chunks, embed_texts([c["embed_text"] for c in chunks], settings), settings)


@pytest.fixture(scope="session")
def sample(settings):
    return _index(settings, ROOT / "data" / "sample")


@pytest.fixture(scope="session")
def docs(sample):
    return sample[0]


@pytest.fixture(scope="session")
def index(sample):
    return sample[1]


@pytest.fixture(scope="session")
def kb(settings):
    if not KB_ZIP.exists():
        pytest.skip("нет data/knowledge-base.zip")
    return _index(settings, KB_ZIP)


@pytest.fixture()
def deps(settings, index):
    return make_local_deps(settings, index, llm_json=lambda messages, max_tokens, default: fake_answer(messages))
