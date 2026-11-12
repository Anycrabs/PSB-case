import type { DocumentSummary } from '../api/types'
import { Composer } from './Composer'
import { documentsWord } from './plural'

const SUGGESTIONS = [
  'Какая арендная плата по договору с ООО «Северный Склад» и до какого числа её вносить?',
  'Какой максимальный срок испытания можно установить главному бухгалтеру?',
  'По какой ставке НДС облагается поставка по договору П-17/2024?',
  'Изменило ли доп. соглашение № 1 условия договора П-17/2024?',
]

interface Props {
  docs: DocumentSummary[] | null
  busy: boolean
  onAsk: (question: string) => void
  onOpenRegistry: () => void
}

export function StartScreen({ docs, busy, onAsk, onOpenRegistry }: Props) {
  return (
    <>
      <div className="start-glow" aria-hidden="true" />
      <section className="start">
        <h1>Ответы по договорам, регламентам и законам со ссылкой на конкретный пункт</h1>
        <p className="start-lead">
          Ассистент работает только с документами базы. Если подтверждения нет, он так и скажет и перечислит, чего
          не хватает.
        </p>
        <Composer busy={busy} placeholder="Например: какой срок оплаты по договору П-17/2024?" onSend={onAsk} autoFocus />
        <div className="suggestions">
          {SUGGESTIONS.map((q) => (
            <button key={q} className="suggestion" onClick={() => onAsk(q)}>
              {q}
            </button>
          ))}
        </div>
        <p className="start-note">
          {docs === null ? (
            'Подключаюсь к базе документов…'
          ) : docs.length ? (
            <>
              В базе {docs.length} {documentsWord(docs.length)}: договоры, регламенты, письма и законы.{' '}
              <button onClick={onOpenRegistry}>Открыть реестр</button>
            </>
          ) : (
            'База документов недоступна. Проверьте, что бэкенд запущен.'
          )}
        </p>
      </section>
    </>
  )
}
