import { useState } from 'react'
import { sendFeedback } from '../api/client'
import type { AnswerStatus, ChatResponse, Source } from '../api/types'
import { Icon, type IconName } from './Icon'

const STATUS: Record<AnswerStatus, { label: string; tone: string; icon: IconName; hint?: string }> = {
  answer: { label: 'Ответ найден в базе', tone: 'ok', icon: 'check' },
  partial: {
    label: 'Частичный ответ',
    tone: 'warn',
    icon: 'info',
    hint: 'В базе есть не всё, что нужно для полного ответа.',
  },
  refusal: {
    label: 'В базе нет ответа',
    tone: '',
    icon: 'ban',
    hint: 'Без подтверждения в документах ассистент не отвечает.',
  },
  clarification: {
    label: 'Нужно уточнение',
    tone: 'info',
    icon: 'help',
    hint: 'Ответьте в поле ниже, ассистент продолжит с учётом вопроса.',
  },
}

interface Props {
  response: ChatResponse
  activeChunkId: string | null
  onOpenSource: (source: Source, siblings: Source[]) => void
}

export function AnswerBlock({ response, activeChunkId, onOpenSource }: Props) {
  const status = STATUS[response.status] ?? STATUS.answer
  const verified = response.verification?.passed
  const issues = response.verification?.issues ?? []
  const [copied, setCopied] = useState(false)
  const [rating, setRating] = useState<'up' | 'down' | null>(null)

  const copy = async () => {
    const refs = response.sources.map((s) => `${s.doc_title}, ${s.citation}`).join('\n')
    try {
      await navigator.clipboard.writeText(refs ? `${response.answer_text}\n\nИсточники:\n${refs}` : response.answer_text)
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      /* буфер обмена недоступен */
    }
  }

  const rate = (value: 'up' | 'down') => {
    setRating(value)
    sendFeedback(response.request_id, value).catch(() => undefined)
  }

  return (
    <article className={`answer s-${response.status}`}>
      <div className="answer-meta">
        <span className={`pill ${status.tone}`}>
          <Icon name={status.icon} size={14} /> {status.label}
        </span>
        {verified !== undefined && response.status !== 'refusal' && (
          <span className={`pill ${verified ? 'ok' : 'warn'}`} title={issues.join('\n') || undefined}>
            <Icon name="shield" size={14} />
            {verified ? 'Сверено с документами' : `Замечания проверки: ${issues.length}`}
          </span>
        )}
        {response.latency_ms > 0 && (
          <span className="answer-time">{(response.latency_ms / 1000).toFixed(1).replace('.', ',')} с</span>
        )}
      </div>

      <p className="answer-text">{response.answer_text}</p>
      {status.hint && <p className="answer-hint">{status.hint}</p>}

      {response.missing_info.length > 0 && (
        <div className="missing">
          <div className="missing-title">
            <Icon name="info" size={16} /> Чего не хватает для полного ответа
          </div>
          <ul>
            {response.missing_info.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      )}

      {response.sources.length > 0 && (
        <div className="sources">
          <div className="sources-title">На чём основан ответ</div>
          <div className="source-list">
            {response.sources.map((s) => (
              <button
                key={s.chunk_id}
                className={`source ${s.chunk_id === activeChunkId ? 'active' : ''}`}
                onClick={() => onOpenSource(s, response.sources)}
                title="Открыть документ на этом месте"
              >
                <span className="source-cite">{s.citation}</span>
                <span className="source-doc">{s.doc_title}</span>
                {s.edition ? (
                  <span className="source-edition">{s.edition}</span>
                ) : (
                  <span className="source-quote">{s.quote}</span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="answer-actions">
        <button className="action" onClick={copy} title="Скопировать ответ вместе с источниками">
          <Icon name={copied ? 'check' : 'copy'} size={15} />
          <span className="copy-label">{copied ? 'Скопировано' : 'Скопировать с источниками'}</span>
        </button>
        <button
          className={`action icon-only ${rating === 'up' ? 'on' : ''}`}
          onClick={() => rate('up')}
          aria-label="Ответ полезен"
          title="Ответ полезен"
        >
          <Icon name="thumbUp" size={15} />
        </button>
        <button
          className={`action icon-only ${rating === 'down' ? 'on' : ''}`}
          onClick={() => rate('down')}
          aria-label="Ответ не помог"
          title="Ответ не помог"
        >
          <Icon name="thumbDown" size={15} />
        </button>
      </div>
    </article>
  )
}
