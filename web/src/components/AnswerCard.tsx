import type { AnswerStatus, ChatResponse, Source } from '../api/types'
import { Icon, type IconName } from './Icon'

const STATUS: Record<AnswerStatus, { label: string; icon: IconName; hint?: string }> = {
  answer: { label: 'Ответ', icon: 'check' },
  partial: { label: 'Частичный ответ', icon: 'info', hint: 'В базе есть не всё, что нужно для полного ответа.' },
  refusal: { label: 'Нет ответа в базе', icon: 'ban' },
  clarification: { label: 'Нужно уточнение', icon: 'help', hint: 'Ответьте в поле ввода ниже.' },
}

interface Props {
  response: ChatResponse
  activeChunkId: string | null
  onOpenSource: (source: Source) => void
}

export function AnswerCard({ response, activeChunkId, onOpenSource }: Props) {
  const status = STATUS[response.status] ?? STATUS.answer
  const issues = response.verification?.issues ?? []
  const verified = response.verification?.passed

  return (
    <article className={`card answer status-${response.status}`}>
      <div className="answer-head">
        <span className={`badge badge-${response.status}`}>
          <Icon name={status.icon} size={14} /> {status.label}
        </span>
        {verified !== undefined && (
          <span
            className={`verify ${verified ? 'ok' : 'warn'}`}
            title={verified ? 'Ссылки и цифры сверены с документами' : issues.join('\n')}
          >
            <Icon name={verified ? 'check' : 'alert'} size={14} />
            {verified ? 'Проверено по документам' : 'Есть замечания проверки'}
          </span>
        )}
      </div>

      <div className="answer-text">{response.answer_text}</div>

      {status.hint && <p className="answer-hint">{status.hint}</p>}

      {response.missing_info.length > 0 && (
        <div className="missing">
          <div className="block-title">
            <Icon name="info" size={16} /> Чего не хватает
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
          <div className="block-title">Источники</div>
          <div className="source-list">
            {response.sources.map((s) => (
              <button
                key={s.chunk_id}
                className={`source-chip ${s.chunk_id === activeChunkId ? 'active' : ''}`}
                onClick={() => onOpenSource(s)}
                title="Открыть документ на этом месте"
              >
                <span className="source-label">{s.id}</span>
                <span className="source-body">
                  <span className="source-doc">{s.doc_title}</span>
                  <span className="source-cite">
                    {s.citation}
                    {s.edition && <span className="source-edition"> · {s.edition}</span>}
                  </span>
                </span>
              </button>
            ))}
          </div>
        </div>
      )}
    </article>
  )
}
