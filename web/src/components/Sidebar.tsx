import { useEffect, useMemo, useState } from 'react'
import { listDialogs } from '../api/client'
import type { DialogSummary, DocumentSummary } from '../api/types'
import { Icon } from './Icon'
import { DOC_TYPE_ORDER, docType } from './docTypes'

const RECENT_LIMIT = 5

// цвет метки группы в реестре: договоры и допсоглашения — латунь, регламенты и письма — синий, законы — зелёный
const TYPE_TONE: Record<string, string> = {
  contract: '#e8c47f',
  supplement: '#f0b884',
  regulation: '#9fb7ee',
  letter: '#b6c4ea',
  law: '#82d4ab',
}

interface Props {
  open: boolean
  docs: DocumentSummary[] | null
  currentDialogId: string | null
  activeDocId: string | null
  /** меняется после каждого ответа — по нему перечитываем недавние вопросы */
  refreshKey: string
  onNewQuestion: () => void
  onFindDocument: () => void
  onOpenDocument: (doc: DocumentSummary) => void
  onSelectDialog: (id: string) => void
  onShowHistory: () => void
  onClose: () => void
}

export function Sidebar(props: Props) {
  const { open, docs, currentDialogId, activeDocId, refreshKey } = props
  const [dialogs, setDialogs] = useState<DialogSummary[] | null>(null)
  const [expanded, setExpanded] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    listDialogs()
      .then((list) => alive && setDialogs([...list].reverse()))
      .catch(() => alive && setDialogs([]))
    return () => {
      alive = false
    }
  }, [refreshKey])

  const groups = useMemo(() => {
    const byType = new Map<string, DocumentSummary[]>()
    for (const d of docs ?? []) byType.set(d.doc_type, [...(byType.get(d.doc_type) ?? []), d])
    const order = [...DOC_TYPE_ORDER, ...[...byType.keys()].filter((t) => !DOC_TYPE_ORDER.includes(t))]
    return order.filter((t) => byType.has(t)).map((t) => ({ type: t, items: byType.get(t)! }))
  }, [docs])

  const recent = dialogs?.slice(0, RECENT_LIMIT) ?? []

  return (
    <>
      {open && <div className="side-scrim" onClick={props.onClose} />}
      <aside className={`side ${open ? 'open' : ''}`} aria-label="Навигация">
        <div className="side-brand">
          <button className="brand" onClick={props.onNewQuestion} title="На начальный экран">
            <span className="brand-mark" aria-hidden="true">
              §
            </span>
            <span className="brand-title">Юридический ассистент</span>
          </button>
          <button className="icon-btn side-close" onClick={props.onClose} aria-label="Закрыть меню">
            <Icon name="close" />
          </button>
        </div>

        <button className="sbtn pri" onClick={props.onNewQuestion}>
          <Icon name="plus" size={17} />
          Новый вопрос
          <kbd>N</kbd>
        </button>
        <button className="sbtn" onClick={props.onFindDocument}>
          <Icon name="search" size={17} />
          Найти документ
          <kbd>/</kbd>
        </button>

        <div className="side-scroll">
          <div className="side-group">
            <span>Реестр</span>
            {docs?.length ? <span className="num">{docs.length}</span> : null}
          </div>
          {docs === null && <p className="side-empty">Загрузка…</p>}
          {docs !== null && docs.length === 0 && <p className="side-empty">База недоступна</p>}
          <ul className="tree">
            {groups.map(({ type, items }) => {
              const isOpen = expanded === type
              return (
                <li key={type}>
                  <button
                    className={`tree-row ${isOpen ? 'open' : ''}`}
                    onClick={() => setExpanded(isOpen ? null : type)}
                    aria-expanded={isOpen}
                  >
                    <i className="tone" style={{ background: TYPE_TONE[type] ?? '#a9b2c9' }} />
                    {docType(type).plural}
                    <span className="num">{items.length}</span>
                  </button>
                  {isOpen && (
                    <ul className="tree-docs">
                      {items.map((d) => (
                        <li key={d.doc_id}>
                          <button
                            className={`tree-doc ${d.doc_id === activeDocId ? 'active' : ''}`}
                            onClick={() => props.onOpenDocument(d)}
                            title={d.title}
                          >
                            {d.title}
                          </button>
                        </li>
                      ))}
                    </ul>
                  )}
                </li>
              )
            })}
          </ul>

          <div className="side-group">
            <span>Недавние</span>
          </div>
          {dialogs !== null && recent.length === 0 && <p className="side-empty">Здесь появятся заданные вопросы</p>}
          <ul className="tree">
            {recent.map((d) => (
              <li key={d.dialog_id}>
                <button
                  className={`tree-row recent ${d.dialog_id === currentDialogId ? 'active' : ''}`}
                  onClick={() => props.onSelectDialog(d.dialog_id)}
                  title={d.title}
                >
                  {d.title || 'Без названия'}
                </button>
              </li>
            ))}
          </ul>
          {(dialogs?.length ?? 0) > RECENT_LIMIT && (
            <button className="side-more" onClick={props.onShowHistory}>
              Вся история ({dialogs!.length})
            </button>
          )}
        </div>

        <div className={`side-ft ${docs === null ? 'wait' : docs.length ? '' : 'down'}`}>
          <i />
          {docs === null
            ? 'Подключаюсь к базе…'
            : docs.length
              ? 'База актуальна, индекс собран'
              : 'База документов недоступна'}
        </div>
      </aside>
    </>
  )
}
