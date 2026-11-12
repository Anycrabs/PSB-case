import type { ChatResponse, DialogMessage, DialogSummary, LegalDocument } from './types'

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

export function listDialogs(): Promise<DialogSummary[]> {
  return request('/dialogs')
}

export function getDialog(dialogId: string): Promise<{ dialog_id: string; messages: DialogMessage[] }> {
  return request(`/dialogs/${encodeURIComponent(dialogId)}`)
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
