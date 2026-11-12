import { useEffect, useRef } from 'react'
import type { ChatResponse, Source } from '../api/types'
import { AnswerCard } from './AnswerCard'
import { Icon } from './Icon'

export interface Turn {
  id: string
  question: string
  state: 'pending' | 'done' | 'error'
  response?: ChatResponse
  error?: string
}

interface Props {
  turns: Turn[]
  activeChunkId: string | null
  onAsk: (question: string) => void
  onRetry: (turnId: string) => void
  onOpenSource: (source: Source) => void
}

const EXAMPLES = [
  'Какая арендная плата по договору с ООО «Северный Склад» и до какого числа её нужно вносить?',
  'Какой срок оплаты товара по договору поставки П-17/2024?',
  'Какой максимальный срок испытания можно установить главному бухгалтеру?',
  'Кто согласует договоры по регламенту ООО «Меридиан»?',
]

export function ChatView({ turns, activeChunkId, onAsk, onRetry, onOpenSource }: Props) {
  const endRef = useRef<HTMLDivElement>(null)
  const last = turns[turns.length - 1]

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, last?.state])

  if (turns.length === 0) {
    return (
      <div className="chat-scroll">
        <div className="empty">
          <div className="empty-icon">
            <Icon name="doc" size={28} />
          </div>
          <h1>Чем помочь?</h1>
          <p className="muted">
            Ассистент ищет ответ в договорах, внутренних регламентах и законах из базы знаний, ссылается на
            конкретные пункты и прямо говорит, если данных не хватает.
          </p>
          <div className="examples">
            {EXAMPLES.map((q) => (
              <button key={q} className="example" onClick={() => onAsk(q)}>
                {q}
              </button>
            ))}
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="chat-scroll">
      <div className="thread">
        {turns.map((turn) => (
          <div key={turn.id} className="turn">
            <div className="user-msg">{turn.question}</div>
            {turn.state === 'pending' && (
              <div className="card pending">
                <span className="dots" aria-hidden="true">
                  <i />
                  <i />
                  <i />
                </span>
                Ищу в базе и проверяю ответ…
              </div>
            )}
            {turn.state === 'error' && (
              <div className="card error-card" role="alert">
                <Icon name="alert" />
                <div>
                  <p>Не удалось получить ответ. {turn.error}</p>
                  <button className="btn btn-outline small" onClick={() => onRetry(turn.id)}>
                    <Icon name="refresh" size={16} /> Повторить
                  </button>
                </div>
              </div>
            )}
            {turn.state === 'done' && turn.response && (
              <AnswerCard response={turn.response} activeChunkId={activeChunkId} onOpenSource={onOpenSource} />
            )}
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  )
}
