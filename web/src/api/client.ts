import type { ChatResponse, DialogMessage, DialogSummary, DocumentSummary, LegalDocument } from './types'

const BASE = '/api/v1'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(BASE + path, {
      ...init,
      headers: { 'Content-Type': 'application/json', ...init?.headers },
    })
  } catch {
    throw new ApiError(0, 'Сервер недоступен. Проверьте, что бэкенд запущен.')
  }
  if (!response.ok) {
    let detail = response.statusText
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch {
      /* тело не JSON */
    }
    throw new ApiError(response.status, detail || `Ошибка ${response.status}`)
  }
  return response.json() as Promise<T>
}

export function sendQuestion(question: string, dialogId: string | null): Promise<ChatResponse> {
  return request('/chat', {
    method: 'POST',
    body: JSON.stringify({ question, dialog_id: dialogId }),
  })
}

export type PipelineStage = 'analyze' | 'retrieve' | 'generate' | 'review' | 'verify'

/**
 * Вопрос через поток /chat/stream: этапы конвейера приходят по мере выполнения (onStage),
 * в конце — проверенный ответ. Если поток недоступен (старый бэкенд, прокси режет SSE),
 * откатывается на обычный /chat.
 */
export async function streamQuestion(
  question: string,
  dialogId: string | null,
  onStage: (stage: PipelineStage) => void,
): Promise<ChatResponse> {
  let response: Response
  try {
    response = await fetch(BASE + '/chat/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ question, dialog_id: dialogId }),
    })
  } catch {
    throw new ApiError(0, 'Сервер недоступен. Проверьте, что бэкенд запущен.')
  }
  if (!response.ok || !response.body) return sendQuestion(question, dialogId)

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (value) buffer += value
    let boundary: number
    while ((boundary = buffer.indexOf('\n\n')) !== -1) {
      const block = buffer.slice(0, boundary)
      buffer = buffer.slice(boundary + 2)
      const event = parseEvent(block)
      if (!event) continue
      if (event.name === 'stage') onStage(event.data.stage as PipelineStage)
      if (event.name === 'result') return event.data as unknown as ChatResponse
      if (event.name === 'error') throw new ApiError(502, String(event.data.message ?? 'Ответ не получен.'))
    }
    if (done) break
  }
  throw new ApiError(502, 'Соединение прервалось до ответа. Попробуйте ещё раз.')
}

function parseEvent(block: string): { name: string; data: Record<string, unknown> } | null {
  let name = 'message'
  const data: string[] = []
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim()
    else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
  }
  if (!data.length) return null
  try {
    return { name, data: JSON.parse(data.join('\n')) }
  } catch {
    return null
  }
}

export function listDialogs(): Promise<DialogSummary[]> {
  return request('/dialogs')
}

export function getDialog(dialogId: string): Promise<{ dialog_id: string; messages: DialogMessage[] }> {
  return request(`/dialogs/${encodeURIComponent(dialogId)}`)
}

export function listDocuments(): Promise<DocumentSummary[]> {
  return request('/documents')
}

export function sendFeedback(requestId: string, rating: 'up' | 'down'): Promise<{ ok: boolean }> {
  return request('/feedback', { method: 'POST', body: JSON.stringify({ request_id: requestId, rating }) })
}

const documentCache = new Map<string, Promise<LegalDocument>>()

export function getDocument(docId: string): Promise<LegalDocument> {
  let cached = documentCache.get(docId)
  if (!cached) {
    cached = request<LegalDocument>(`/documents/${encodeURIComponent(docId)}`)
    cached.catch(() => documentCache.delete(docId))
    documentCache.set(docId, cached)
  }
  return cached
}
