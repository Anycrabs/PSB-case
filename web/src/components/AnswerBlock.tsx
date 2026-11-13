import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { sendFeedback } from '../api/client'
import type { AnswerStatus, ChatResponse, Source } from '../api/types'
import { Icon, type IconName } from './Icon'

const STATUS: Record<AnswerStatus, { label: string; tone: string; icon: IconName; hint?: string }> = {
  answer: { label: 'Ответ найден в базе', tone: 'ok', icon: 'check' },
  partial: {
    label: 'Частичный ответ',
    tone: 'partial',
    icon: 'info',
    hint: 'В базе есть не всё, что нужно для полного ответа.',
  },
  refusal: {
    label: 'В базе нет ответа',
    tone: 'refusal',
    icon: 'ban',
    hint: 'Без подтверждения в документах ассистент не отвечает.',
  },
  clarification: {
    label: 'Нужно уточнение',
    tone: 'clarify',
    icon: 'help',
    hint: 'Ответьте в поле ниже, ассистент продолжит с учётом вопроса.',
  },
}

const MARKER_RE = /\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]/g
const WIDE_QUERY = '(min-width: 1001px)'

const isVoid = (edition: string) => /утрат|не\s*действ|недейств/i.test(edition)

interface Props {
  response: ChatResponse
  activeChunkId: string | null
  onOpenSource: (source: Source, siblings: Source[]) => void
}

/** Лист ответа и пометки-источники на полях. Возвращает две ячейки сетки .folio. */
export function AnswerBlock({ response, activeChunkId, onOpenSource }: Props) {
  const status = STATUS[response.status] ?? STATUS.answer
  const sources = response.sources
  const verified = response.verification?.passed
  const issues = response.verification?.issues ?? []
  const [copied, setCopied] = useState(false)
  const [rating, setRating] = useState<'up' | 'down' | null>(null)
  const sheetRef = useRef<HTMLElement>(null)
  const marginRef = useRef<HTMLElement>(null)

  const numberOf = new Map(sources.map((s, i) => [s.id, i + 1]))
  const open = (s: Source) => onOpenSource(s, sources)

  // Пометки на полях встают напротив строки, где стоит номер ссылки, и не налезают друг на друга.
  useLayoutEffect(() => {
    const sheet = sheetRef.current
    const margin = marginRef.current
    if (!sheet || !margin) return
    const wide = window.matchMedia(WIDE_QUERY)
    const layout = () => {
      const notes = [...margin.querySelectorAll<HTMLElement>('.note')]
      if (!wide.matches) {
        notes.forEach((n) => (n.style.top = ''))
        margin.style.minHeight = ''
        return
      }
      const base = margin.getBoundingClientRect().top
      let floor = 0
      for (const note of notes) {
        const ref = sheet.querySelector<HTMLElement>(`.ref [data-label="${note.dataset.label}"]`)
        const want = ref ? ref.getBoundingClientRect().top - base - 9 : floor
        const top = Math.max(want, floor)
        note.style.top = `${top}px`
        floor = top + note.offsetHeight + 8
      }
      margin.style.minHeight = `${floor}px`
    }
    layout()
    const observer = new ResizeObserver(layout)
    observer.observe(sheet)
    wide.addEventListener('change', layout)
    void document.fonts?.ready.then(layout)
    return () => {
      observer.disconnect()
      wide.removeEventListener('change', layout)
    }
  }, [response])

  const copy = async () => {
    const refs = sources.map((s, i) => `${i + 1}. ${s.doc_title}, ${s.citation}`).join('\n')
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

  const renderParagraph = (paragraph: string, key: number) => {
    const nodes: ReactNode[] = []
    let last = 0
    for (const match of paragraph.matchAll(MARKER_RE)) {
      nodes.push(paragraph.slice(last, match.index))
      last = (match.index ?? 0) + match[0].length
      const labels = [...new Set(match[1].split(/\s*[,;]\s*/))].filter((l) => numberOf.has(l))
      for (const label of labels) {
        const source = sources[numberOf.get(label)! - 1]
        nodes.push(
          <sup className="ref" key={`${match.index}-${label}`}>
            <button
              data-label={label}
              className={source.chunk_id === activeChunkId ? 'on' : ''}
              onClick={() => open(source)}
              aria-label={`Источник ${numberOf.get(label)}: ${source.doc_title}, ${source.citation}`}
            >
              {numberOf.get(label)}
            </button>
          </sup>,
        )
      }
    }
    nodes.push(paragraph.slice(last))
    return <p key={key}>{nodes}</p>
  }

  const text = response.answer_marked || response.answer_text
  const paragraphs = text.split(/\n+/).filter((p) => p.trim())

  return (
    <>
      <article ref={sheetRef} className={`sheet glass s-${response.status}`}>
        <div className={`status ${status.tone}`}>
          <Icon name={status.icon} size={16} />
          {status.label}
          <span className="actions">
            {sources.length > 0 && (
              <button className="act" onClick={copy} title="Скопировать ответ вместе со списком источников">
                <Icon name={copied ? 'check' : 'copy'} size={16} />
                <span className="copy-label">{copied ? 'Скопировано' : 'Скопировать'}</span>
              </button>
            )}
            <button
              className={`act icon ${rating === 'up' ? 'on' : ''}`}
              onClick={() => rate('up')}
              aria-label="Ответ полезен"
              title="Ответ полезен"
            >
              <Icon name="thumbUp" size={16} />
            </button>
            <button
              className={`act icon ${rating === 'down' ? 'on' : ''}`}
              onClick={() => rate('down')}
              aria-label="Ответ не помог"
              title="Ответ не помог"
            >
              <Icon name="thumbDown" size={16} />
            </button>
          </span>
        </div>

        <div className="answer">{paragraphs.map(renderParagraph)}</div>

        {response.missing_info.length > 0 && (
          <div className="missing">
            <h4>Чего не хватает для полного ответа</h4>
            <ul>
              {response.missing_info.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        )}

        <div className="visa">
          {verified !== undefined && response.status !== 'refusal' && sources.length > 0 && (
            <span className={`stamp ${verified ? '' : 'warn'}`} title={issues.join('\n') || undefined}>
              <Icon name={verified ? 'check' : 'alert'} size={14} />
              {verified ? 'Сверено с документами' : `Замечания проверки: ${issues.length}`}
            </span>
          )}
          {status.hint ? (
            <span>{status.hint}</span>
          ) : (
            sources.length > 0 && <span>Ссылок на документы: {sources.length}</span>
          )}
          {response.latency_ms > 0 && (
            <span className="time">{(response.latency_ms / 1000).toFixed(1).replace('.', ',')} с</span>
          )}
        </div>
      </article>

      <aside ref={marginRef} className="margin" aria-label={sources.length ? 'Источники ответа' : undefined}>
        {sources.map((s, i) => (
          <button
            key={s.chunk_id}
            data-label={s.id}
            className={`note ${s.chunk_id === activeChunkId ? 'on' : ''}`}
            onClick={() => open(s)}
            title="Открыть документ на этом месте"
          >
            <span className="note-n">{i + 1}</span>
            <span className="note-doc">{s.doc_title}</span>
            <span className="note-cite">{s.citation}</span>
            {s.edition && <span className={`note-ed ${isVoid(s.edition) ? 'void' : ''}`}>{s.edition}</span>}
          </button>
        ))}
      </aside>
    </>
  )
}
