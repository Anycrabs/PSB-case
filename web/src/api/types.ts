// Типы повторяют контракты из legal_core/schemas.py и ответы services/gateway.

export type AnswerStatus = 'answer' | 'partial' | 'refusal' | 'clarification'

export interface Source {
  id: string // метка S1..Sn
  chunk_id: string
  doc_id: string
  doc_title: string
  citation: string // «п. 4.2», «ст. 164»
  edition: string // редакция: «действует», «утратила силу с …»
  quote: string
  char_span: [number, number] // смещения цитаты в тексте документа
}

export interface Verification {
  passed?: boolean
  issues?: string[]
}

export interface ChatResponse {
  request_id: string
  dialog_id: string | null
  status: AnswerStatus
  answer_text: string
  answer_marked?: string // тот же ответ с метками [S1]; в старых диалогах может отсутствовать
  missing_info: string[]
  sources: Source[]
  verification: Verification
  trace: Record<string, unknown>
  latency_ms: number
}

export interface DialogSummary {
  dialog_id: string
  title: string
  messages: number
}

export interface DialogMessage {
  role: 'user' | 'assistant'
  content: string
  response?: ChatResponse
}

export interface DocumentChunkRef {
  chunk_id: string
  citation: string
  char_span: [number, number]
}

export interface LegalDocument {
  doc_id: string
  title: string
  doc_type: string
  text: string
  chunks: DocumentChunkRef[]
}

export interface DocumentSummary {
  doc_id: string
  title: string
  doc_type: string
  chunks: number
}
