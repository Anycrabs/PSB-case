import { useEffect, useRef, useState } from 'react'
import { getDocument } from '../api/client'
import type { LegalDocument, Source } from '../api/types'
import { Icon } from './Icon'

const DOC_TYPES: Record<string, string> = {
  contract: 'Договор',
  supplement: 'Доп. соглашение',
  regulation: 'Регламент',
  letter: 'Письмо / записка',
  law: 'Закон',
}

interface Props {
  source: Source
  onClose: () => void
}

export function DocumentDrawer({ source, onClose }: Props) {
  const [doc, setDoc] = useState<LegalDocument | null>(null)
  const [failure, setFailure] = useState<{ docId: string; message: string } | null>(null)
  const markRef = useRef<HTMLElement>(null)

  useEffect(() => {
    let cancelled = false
    getDocument(source.doc_id)
      .then((d) => !cancelled && setDoc(d))
      .catch((e: Error) => !cancelled && setFailure({ docId: source.doc_id, message: e.message }))
    return () => {
      cancelled = true
    }
  }, [source.doc_id])

  const current = doc?.doc_id === source.doc_id ? doc : null
  const error = failure?.docId === source.doc_id ? failure.message : null

  useEffect(() => {
    markRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [current, source.chunk_id])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside className="drawer" aria-label="Просмотр документа">
      <div className="drawer-head">
        <div className="drawer-meta">
          <span className="drawer-type">{current ? (DOC_TYPES[current.doc_type] ?? current.doc_type) : 'Документ'}</span>
          <h2>{source.doc_title}</h2>
          <div className="drawer-cite">
            <span className="source-label">{source.id}</span> {source.citation}
            {source.edition && <span className="muted"> · {source.edition}</span>}
          </div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label="Закрыть документ">
          <Icon name="close" />
        </button>
      </div>
      <div className="drawer-body">
        {error && <p className="error-text">Не удалось открыть документ: {error}</p>}
        {!error && !current && <p className="muted">Загрузка документа…</p>}
        {current && <DocumentText text={current.text} span={source.char_span} markRef={markRef} />}
      </div>
    </aside>
  )
}

function DocumentText({
  text,
  span,
  markRef,
}: {
  text: string
  span: [number, number]
  markRef: React.RefObject<HTMLElement | null>
}) {
  const [start, end] = span
  const valid = start >= 0 && end > start && end <= text.length
  if (!valid) return <div className="doc-text">{text}</div>
  return (
    <div className="doc-text">
      {text.slice(0, start)}
      <mark ref={markRef}>{text.slice(start, end)}</mark>
      {text.slice(end)}
    </div>
  )
}
