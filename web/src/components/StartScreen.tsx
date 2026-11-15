import { useEffect, useState, type KeyboardEvent } from 'react'
import type { DocumentSummary } from '../api/types'
import { Composer } from './Composer'
import { Icon, type IconName } from './Icon'
import { documentsDative } from './plural'

// В строке — короткая формулировка, в ассистента уходит полный вопрос.
const EXAMPLES: { kind: 'c' | 'r' | 'l'; icon: IconName; label: string; source: string; question: string }[] = [
  {
    kind: 'c',
    icon: 'briefcase',
    label: 'Арендная плата по договору с ООО «Северный Склад» и срок её внесения',
    source: 'Договор А-08/2024',
    question: 'Какая арендная плата по договору с ООО «Северный Склад» и до какого числа её вносить?',
  },
  {
    kind: 'r',
    icon: 'book',
    label: 'Нужно ли одобрение гендиректора для договора на 12 млн в августе 2024?',
    source: 'Регламент, ред. 2023',
    question: 'Договор на 12 млн рублей заключали в августе 2024 года. Нужно ли было одобрение генерального директора?',
  },
  {
    kind: 'l',
    icon: 'scale',
    label: 'Максимальный срок испытания для главного бухгалтера',
    source: 'ТК РФ, ст. 70',
    question: 'Какой максимальный срок испытания можно установить главному бухгалтеру?',
  },
  {
    kind: 'r',
    icon: 'doc',
    label: 'Санкционная проверка договора с иностранной компанией',
    source: 'Письмо К-12',
    question: 'Нужна ли санкционная проверка для договора с иностранной компанией?',
  },
]

interface Props {
  docs: DocumentSummary[] | null
  busy: boolean
  onAsk: (question: string) => void
}

const isTyping = (el: EventTarget | null) =>
  el instanceof HTMLElement && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)

export function StartScreen({ docs, busy, onAsk }: Props) {
  // -1 — ничего не выбрано: Enter в пустом поле ничего не отправит, пока пример не выбран стрелкой
  const [sel, setSel] = useState(-1)

  // цифры 1–4 работают, когда курсор не в поле ввода (иначе мешали бы набирать «152-ФЗ…»)
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (busy || e.metaKey || e.ctrlKey || e.altKey || isTyping(e.target)) return
      const n = Number(e.key)
      if (n >= 1 && n <= EXAMPLES.length) {
        e.preventDefault()
        onAsk(EXAMPLES[n - 1].question)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [busy, onAsk])

  // стрелки и Enter, пока поле пустое, управляют списком примеров
  const onPaletteKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (!(e.target instanceof HTMLTextAreaElement) || e.target.value !== '') return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const step = e.key === 'ArrowDown' ? 1 : -1
      setSel((i) => (i + step + EXAMPLES.length) % EXAMPLES.length)
    } else if (e.key === 'Enter' && !e.shiftKey && sel >= 0 && !busy) {
      onAsk(EXAMPLES[sel].question)
    } else if (e.key === 'Escape') {
      setSel(-1)
    }
  }

  return (
    <section className="start">
      <h1>Что нужно найти в документах?</h1>
      <p className="start-lead">
        Ассистент отвечает по договорам, регламентам и законам из базы и показывает пункт, на котором держится ответ.
      </p>

      <div className="palette" onKeyDown={onPaletteKey}>
        <div className="palette-in">
          <span className="palette-icon">
            <Icon name="search" size={19} />
          </span>
          <Composer busy={busy} placeholder="Например: срок оплаты по договору П-17/2024" onSend={onAsk} autoFocus />
        </div>

        <div className="palette-sec">
          <span>Примеры</span>
          <span className="palette-sec-hint">выберите стрелками или цифрой</span>
        </div>
        <div role="listbox" aria-label="Примеры вопросов">
          {EXAMPLES.map((ex, i) => (
            <button
              key={ex.question}
              role="option"
              aria-selected={sel === i}
              className={`ex-row ${sel === i ? 'sel' : ''}`}
              onClick={() => onAsk(ex.question)}
              onMouseEnter={() => setSel(i)}
              disabled={busy}
              title={ex.question}
            >
              <span className={`kind k-${ex.kind}`}>
                <Icon name={ex.icon} size={16} />
              </span>
              <span className="ex-text">{ex.label}</span>
              <span className="ex-src">{ex.source}</span>
              <kbd>{i + 1}</kbd>
            </button>
          ))}
        </div>

        <div className="palette-foot">
          <span>
            <kbd>↑</kbd>
            <kbd>↓</kbd> выбрать
          </span>
          <span>
            <kbd>⏎</kbd> спросить
          </span>
          <span className="palette-base">
            <Icon name="book" size={15} />
            {docs === null
              ? 'Подключаюсь к базе…'
              : docs.length
                ? `Поиск по ${docs.length} ${documentsDative(docs.length)} базы`
                : 'База документов недоступна'}
          </span>
        </div>
      </div>

      <ul className="promises">
        <li>
          <Icon name="target" size={16} />
          Ссылка на документ, пункт и редакцию
        </li>
        <li>
          <Icon name="shield" size={16} />
          Цифры и сроки сверяются с текстом
        </li>
        <li>
          <Icon name="ban" size={16} />
          Без подтверждения — честный отказ
        </li>
      </ul>
    </section>
  )
}
