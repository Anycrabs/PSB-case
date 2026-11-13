import { useEffect, useRef, useState } from 'react'
import { getDocument } from '../api/client'
import type { DocumentChunkRef, LegalDocument, Source } from '../api/types'
import { Icon } from './Icon'
import { docType } from './docTypes'

export interface DrawerTarget {
  docId: string
  title: string
  source?: Source // если открыт из ответа — подсвечиваем фрагмент
  siblings: Source[] // все источники ответа, для переключения S1…Sn
}

interface Props {
  target: DrawerTarget
  onNavigate: (target: DrawerTarget) => void
  onClose: () => void
}

export function DocumentDrawer({ target, onNavigate, onClose }: Props) {
  const [doc, setDoc] = useState<LegalDocument | null>(null)
  const [failure, setFailure] = useState<{ docId: string; message: string } | null>(null)
  const markRef = useRef<HTMLElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)
  const anchorsRef = useRef(new Map<string, HTMLElement>())
  const { source, siblings } = target

  useEffect(() => {
    let cancelled = false
    getDocument(target.docId)
      .then((d) => !cancelled && setDoc(d))
      .catch((e: Error) => !cancelled && setFailure({ docId: target.docId, message: e.message }))
    return () => {
      cancelled = true
    }
  }, [target.docId])

  const current = doc?.doc_id === target.docId ? doc : null
  const error = failure?.docId === target.docId ? failure.message : null

  const scrollToMark = () => markRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })

  useEffect(() => {
    if (source) scrollToMark()
    else bodyRef.current?.scrollTo({ top: 0 })
  }, [current, source])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const index = source ? siblings.findIndex((s) => s.chunk_id === source.chunk_id) : -1
  const go = (i: number) => {
    const s = siblings[i]
    if (s) onNavigate({ docId: s.doc_id, title: s.doc_title, source: s, siblings })
  }
  const meta = current ? docType(current.doc_type) : null

  return (
    <aside className="drawer" aria-label="Просмотр документа">
      <div className="drawer-head">
        <div className="drawer-meta">
          <div className="drawer-kind">
            {meta ? meta.label : 'Документ'}
            {source?.edition && `, ${source.edition}`}
          </div>
          <h2>{target.title}</h2>
          {source && (
            <div className="drawer-cite">
              <span className="cite-chip">{source.citation}</span>
              <button onClick={scrollToMark}>Показать выделенный фрагмент</button>
            </div>
          )}
        </div>
        <div className="drawer-controls">
          {siblings.length > 1 && (
            <>
              <button className="icon-btn" onClick={() => go(index - 1)} disabled={index <= 0} aria-label="Предыдущий источник">
                <Icon name="chevronLeft" />
              </button>
              <button
                className="icon-btn"
                onClick={() => go(index + 1)}
                disabled={index < 0 || index >= siblings.length - 1}
                aria-label="Следующий источник"
              >
                <Icon name="chevronRight" />
              </button>
            </>
          )}
          <button className="icon-btn" onClick={onClose} aria-label="Закрыть документ">
            <Icon name="close" />
          </button>
        </div>
      </div>

      {siblings.length > 1 && (
        <div className="drawer-sources" role="tablist" aria-label="Источники ответа">
          {siblings.map((s, i) => (
            <button
              key={s.chunk_id}
              role="tab"
              aria-selected={i === index}
              className={`drawer-source ${i === index ? 'active' : ''}`}
              onClick={() => go(i)}
              title={`${s.doc_title}, ${s.citation}`}
            >
              <span className="drawer-source-n">{i + 1}</span>
              {s.citation}
            </button>
          ))}
        </div>
      )}

      <div className="drawer-content">
        <div className="drawer-body" ref={bodyRef}>
          {error && <p className="error-text">Не удалось открыть документ: {error}</p>}
          {!error && !current && <div className="skeleton" aria-label="Загрузка документа" />}
          {current && (
            <article>
              <DocumentText
                text={current.text}
                chunks={current.chunks}
                span={source?.char_span}
                markRef={markRef}
                anchorsRef={anchorsRef}
              />
            </article>
          )}
        </div>
        {current && current.chunks.length > 1 && (
          <DocumentMap
            chunks={current.chunks}
            activeChunkId={source?.chunk_id ?? null}
            onJump={(chunkId) =>
              anchorsRef.current.get(chunkId)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
            }
          />
        )}
      </div>
    </aside>
  )
}

// Текст режется по началам фрагментов (якоря для карты) и по границам цитаты (подсветка).
function DocumentText({
  text,
  chunks,
  span,
  markRef,
  anchorsRef,
}: {
  text: string
  chunks: DocumentChunkRef[]
  span?: [number, number]
  markRef: React.RefObject<HTMLElement | null>
  anchorsRef: React.RefObject<Map<string, HTMLElement>>
}) {
  const validSpan = span && span[0] >= 0 && span[1] > span[0] && span[1] <= text.length ? span : null
  const starts = new Map<number, string>()
  for (const c of chunks) {
    const at = c.char_span?.[0]
    if (at >= 0 && at < text.length && !starts.has(at)) starts.set(at, c.chunk_id)
  }
  const cuts = new Set<number>([0, text.length, ...starts.keys()])
  if (validSpan) {
    cuts.add(validSpan[0])
    cuts.add(validSpan[1])
  }
  const points = [...cuts].sort((a, b) => a - b)

  const nodes: React.ReactNode[] = []
  let markAssigned = false
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i]
    const b = points[i + 1]
    const chunkId = starts.get(a)
    if (chunkId) {
      nodes.push(
        <span
          key={`a${a}`}
          className="chunk-anchor"
          ref={(el) => {
            if (el) anchorsRef.current.set(chunkId, el)
            else anchorsRef.current.delete(chunkId)
          }}
        />,
      )
    }
    const piece = text.slice(a, b)
    const inMark = validSpan && a >= validSpan[0] && b <= validSpan[1]
    if (inMark) {
      const first = !markAssigned
      markAssigned = true
      nodes.push(
        <mark key={`m${a}`} ref={first ? markRef : undefined}>
          {piece}
        </mark>,
      )
    } else {
      nodes.push(piece)
    }
  }
  return <div className="doc-text">{nodes}</div>
}

// Карта документа: полоса из фрагментов пропорционально их длине.
function DocumentMap({
  chunks,
  activeChunkId,
  onJump,
}: {
  chunks: DocumentChunkRef[]
  activeChunkId: string | null
  onJump: (chunkId: string) => void
}) {
  return (
    <nav className="doc-map" aria-label="Карта документа">
      {chunks.map((c) => {
        const length = Math.max(1, (c.char_span?.[1] ?? 0) - (c.char_span?.[0] ?? 0))
        const active = c.chunk_id === activeChunkId
        return (
          <button
            key={c.chunk_id}
            className={`map-seg ${active ? 'active' : ''}`}
            style={{ flexGrow: length }}
            onClick={() => onJump(c.chunk_id)}
            aria-label={`Перейти: ${c.citation}`}
          >
            <span className="map-tip">{c.citation}</span>
          </button>
        )
      })}
    </nav>
  )
}
