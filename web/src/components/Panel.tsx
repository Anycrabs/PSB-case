import { useEffect, useMemo, useState } from 'react'
import { listDialogs } from '../api/client'
import type { DialogSummary, DocumentSummary } from '../api/types'
import { Icon } from './Icon'
import { DOC_TYPE_ORDER, docType } from './docTypes'

export type PanelTab = 'history' | 'registry'

interface Props {
  tab: PanelTab
  docs: DocumentSummary[] | null
  currentDialogId: string | null
  activeDocId: string | null
  onTab: (tab: PanelTab) => void
  onSelectDialog: (id: string) => void
  onOpenDocument: (doc: DocumentSummary) => void
  onClose: () => void
}

export function Panel(props: Props) {
  const { tab, docs, currentDialogId, activeDocId, onTab, onSelectDialog, onOpenDocument, onClose } = props

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <>
      <div className="panel-backdrop" onClick={onClose} />
      <aside className="panel" aria-label={tab === 'history' ? 'История вопросов' : 'Реестр документов'}>
        <div className="panel-head">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'history'} className={`tab ${tab === 'history' ? 'active' : ''}`} onClick={() => onTab('history')}>
              История
            </button>
            <button role="tab" aria-selected={tab === 'registry'} className={`tab ${tab === 'registry' ? 'active' : ''}`} onClick={() => onTab('registry')}>
              Реестр документов
            </button>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        <div className="panel-body">
          {tab === 'history' ? (
            <History currentDialogId={currentDialogId} onSelect={onSelectDialog} />
          ) : (
            <Registry docs={docs} activeDocId={activeDocId} onOpen={onOpenDocument} />
          )}
        </div>
      </aside>
    </>
  )
}

const HISTORY_PREVIEW = 5

function History({ currentDialogId, onSelect }: { currentDialogId: string | null; onSelect: (id: string) => void }) {
  const [dialogs, setDialogs] = useState<DialogSummary[] | null>(null)
  const [showAll, setShowAll] = useState(false)

  useEffect(() => {
    listDialogs()
      .then((list) => setDialogs([...list].reverse()))
      .catch(() => setDialogs([]))
  }, [])

  if (dialogs === null) return <p className="panel-empty">Загрузка…</p>
  if (dialogs.length === 0) return <p className="panel-empty">Здесь появятся заданные вопросы.</p>

  const visible = showAll ? dialogs : dialogs.slice(0, HISTORY_PREVIEW)
  return (
    <>
      <div className="panel-section-title">{showAll ? 'Все вопросы' : 'Последние вопросы'}</div>
      {visible.map((d) => (
        <button
          key={d.dialog_id}
          className={`history-item ${d.dialog_id === currentDialogId ? 'active' : ''}`}
          onClick={() => onSelect(d.dialog_id)}
          title={d.title}
        >
          <span className="clamp-2">{d.title ? (d.title.length >= 80 ? `${d.title.trimEnd()}…` : d.title) : 'Без названия'}</span>
        </button>
      ))}
      {dialogs.length > HISTORY_PREVIEW && (
        <button className="show-more" onClick={() => setShowAll((v) => !v)}>
          {showAll ? 'Свернуть' : `Показать все (${dialogs.length})`}
        </button>
      )}
    </>
  )
}

function Registry({
  docs,
  activeDocId,
  onOpen,
}: {
  docs: DocumentSummary[] | null
  activeDocId: string | null
  onOpen: (doc: DocumentSummary) => void
}) {
  const [query, setQuery] = useState('')
  // по умолчанию раскрыта только группа открытого сейчас документа
  const [expanded, setExpanded] = useState<Set<string>>(() => {
    const active = docs?.find((d) => d.doc_id === activeDocId)
    return new Set(active ? [active.doc_type] : [])
  })
  const searching = query.trim() !== ''

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    const byType = new Map<string, DocumentSummary[]>()
    for (const d of docs ?? []) {
      if (q && !d.title.toLowerCase().includes(q)) continue
      byType.set(d.doc_type, [...(byType.get(d.doc_type) ?? []), d])
    }
    const order = [...DOC_TYPE_ORDER, ...[...byType.keys()].filter((t) => !DOC_TYPE_ORDER.includes(t))]
    return order.filter((t) => byType.has(t)).map((t) => ({ type: t, items: byType.get(t)! }))
  }, [docs, query])

  const toggle = (type: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(type)) next.delete(type)
      else next.add(type)
      return next
    })

  return (
    <>
      <label className="search">
        <Icon name="search" size={16} />
        <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Найти документ" autoFocus />
      </label>
      {docs === null && <p className="panel-empty">Загрузка…</p>}
      {docs !== null && groups.length === 0 && <p className="panel-empty">Ничего не найдено.</p>}
      {groups.map(({ type, items }) => {
        const open = searching || expanded.has(type)
        return (
          <div key={type} className="kb-group">
            <button className={`kb-group-head ${open ? 'open' : ''}`} onClick={() => toggle(type)} aria-expanded={open}>
              <span className="kb-chevron">
                <Icon name="chevronRight" size={15} />
              </span>
              <span className="kb-group-name">{docType(type).plural}</span>
              <span className="kb-group-count">{items.length}</span>
            </button>
            {open && (
              <div className="kb-items">
                {items.map((d) => (
                  <button
                    key={d.doc_id}
                    className={`kb-doc ${d.doc_id === activeDocId ? 'active' : ''}`}
                    onClick={() => onOpen(d)}
                  >
                    {d.title}
                  </button>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </>
  )
}
