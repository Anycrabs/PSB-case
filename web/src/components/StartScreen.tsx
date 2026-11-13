import type { DocumentSummary } from '../api/types'
import { Composer } from './Composer'
import { DOC_TYPE_ORDER, docType } from './docTypes'
import { documentsWord } from './plural'

const SUGGESTIONS: { tag: string; text: string }[] = [
  { tag: 'Договор', text: 'Какая арендная плата по договору с ООО «Северный Склад» и до какого числа её вносить?' },
  { tag: 'Регламент', text: 'Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было одобрение генерального директора?' },
  { tag: 'ТК РФ', text: 'Какой максимальный срок испытания можно установить главному бухгалтеру?' },
  { tag: 'Письмо', text: 'Нужна ли санкционная проверка для договора с иностранной компанией?' },
  { tag: 'Доп. соглашение', text: 'Изменило ли доп. соглашение № 1 условия договора П-17/2024?' },
]

interface Props {
  docs: DocumentSummary[] | null
  busy: boolean
  onAsk: (question: string) => void
  onOpenRegistry: () => void
}

function baseSummary(docs: DocumentSummary[]) {
  const counts = new Map<string, number>()
  for (const d of docs) counts.set(d.doc_type, (counts.get(d.doc_type) ?? 0) + 1)
  const order = [...DOC_TYPE_ORDER, ...[...counts.keys()].filter((t) => !DOC_TYPE_ORDER.includes(t))]
  return order
    .filter((t) => counts.has(t))
    .map((t) => `${docType(t).plural.toLowerCase()}: ${counts.get(t)}`)
    .join(', ')
}

export function StartScreen({ docs, busy, onAsk, onOpenRegistry }: Props) {
  return (
    <section className="start">
      <h1>Спросите о договоре, регламенте или законе</h1>
      <p className="start-lead">
        Ответ строится только по документам базы. У каждого утверждения есть ссылка на документ, пункт и редакцию. Если
        подтверждения нет, ассистент так и скажет.
      </p>
      <Composer busy={busy} placeholder="Например: срок оплаты по договору П-17/2024" onSend={onAsk} autoFocus />
      <p className="composer-hint">Enter — отправить, Shift + Enter — новая строка</p>

      <div className="examples">
        <h2>Можно начать с этих вопросов</h2>
        <div className="chips">
          {SUGGESTIONS.map((s) => (
            <button key={s.text} className="chip glass" onClick={() => onAsk(s.text)} disabled={busy}>
              <small>{s.tag}</small>
              {s.text}
            </button>
          ))}
        </div>
      </div>

      <p className="base-line">
        {docs === null ? (
          'Подключаюсь к базе документов…'
        ) : docs.length ? (
          <>
            В базе {docs.length} {documentsWord(docs.length)}: {baseSummary(docs)}.{' '}
            <button className="link" onClick={onOpenRegistry}>
              Открыть список
            </button>
          </>
        ) : (
          'База документов недоступна. Проверьте, что бэкенд запущен.'
        )}
      </p>
    </section>
  )
}
