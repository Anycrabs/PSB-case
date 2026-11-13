"""Контракты HTTP API (Pydantic используется только на границе сервисов; ядро работает со словарями)."""

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Message(BaseModel):
    role: Literal["user", "assistant", "system"] = "user"
    content: str


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    dialog_id: Optional[str] = None
    history: list[Message] = []


class Source(BaseModel):
    id: str
    chunk_id: str
    doc_id: str
    doc_title: str
    citation: str
    edition: str = ""
    quote: str
    char_span: list[int]


class ChatResponse(BaseModel):
    request_id: str
    dialog_id: Optional[str] = None
    status: Literal["answer", "partial", "refusal", "clarification"]
    answer_text: str
    answer_marked: str = ""  # тот же ответ с метками [S#] — для ссылок в интерфейсе
    missing_info: list[str] = []
    sources: list[Source] = []
    verification: dict = {}
    trace: dict = {}
    latency_ms: int = 0


class EmbedRequest(BaseModel):
    texts: list[str]


class EmbedResponse(BaseModel):
    dense: list[list[float]]
    sparse: Optional[list[dict[str, float]]] = None


class SearchRequest(BaseModel):
    queries: list[str]
    filters: dict = {}


class AnswerRequest(BaseModel):
    question: str
    history: list[Message] = []
