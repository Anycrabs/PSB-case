import { useEffect, useState } from 'react'
import { listDialogs } from '../api/client'
import type { DialogSummary } from '../api/types'
import { Icon } from './Icon'

interface Props {
  open: boolean
  currentId: string | null
  onSelect: (id: string) => void
  onNewDialog: () => void
  onClose: () => void
}

export function DialogMenu({ open, currentId, onSelect, onNewDialog, onClose }: Props) {
  const [dialogs, setDialogs] = useState<DialogSummary[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    listDialogs()
      .then((list) => {
        setDialogs([...list].reverse())
        setError(null)
      })
      .catch((e: Error) => setError(e.message))
  }, [open])

  if (!open) return null

  return (
    <>
      <div className="menu-backdrop" onClick={onClose} />
      <aside className="dialog-menu" aria-label="Диалоги">
        <div className="dialog-menu-head">
          <span>Диалоги</span>
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть">
            <Icon name="close" />
          </button>
        </div>
        <button className="btn btn-outline full" onClick={onNewDialog}>
          <Icon name="plus" /> Новый диалог
        </button>
        <div className="dialog-list">
          {error && <p className="muted small">{error}</p>}
          {!error && dialogs === null && <p className="muted small">Загрузка…</p>}
          {dialogs?.length === 0 && <p className="muted small">Диалогов пока нет</p>}
          {dialogs?.map((d) => (
            <button
              key={d.dialog_id}
              className={`dialog-item ${d.dialog_id === currentId ? 'active' : ''}`}
              onClick={() => onSelect(d.dialog_id)}
            >
              <Icon name="chat" size={16} />
              <span className="dialog-title">{d.title || 'Без названия'}</span>
            </button>
          ))}
        </div>
      </aside>
    </>
  )
}
