import { useCallback, useEffect, useState } from 'react'
import { getDialog, listDocuments, streamQuestion, type PipelineStage } from './api/client'
import type { ChatResponse, DocumentSummary, Source } from './api/types'
import { Composer } from './components/Composer'
import { Icon } from './components/Icon'
import { StartScreen } from './components/StartScreen'
import { Panel, type PanelTab } from './components/Panel'
import { Sidebar } from './components/Sidebar'
import { AnswerBlock } from './components/AnswerBlock'
import { Backdrop } from './components/Backdrop'
import { PendingSteps } from './components/PendingSteps'
import { DocumentDrawer, type DrawerTarget } from './components/DocumentDrawer'

interface Turn {
  id: string
  question: string
  state: 'pending' | 'done' | 'error'
  stages: PipelineStage[]
  response?: ChatResponse
  error?: string
}

let turnSeq = 0
const nextTurnId = () => `t${++turnSeq}`

export default function App() {
  const [dialogId, setDialogId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null)
  const [panel, setPanel] = useState<PanelTab | null>(null)
  const [docs, setDocs] = useState<DocumentSummary[] | null>(null)
  const [sideOpen, setSideOpen] = useState(false)

  useEffect(() => {
    listDocuments()
      .then(setDocs)
      .catch(() => setDocs([]))
  }, [])

  const busy = turns.some((t) => t.state === 'pending')

  const runTurn = useCallback(async (turnId: string, question: string, currentDialog: string | null) => {
    try {
      const onStage = (stage: PipelineStage) =>
        setTurns((all) => all.map((t) => (t.id === turnId ? { ...t, stages: [...t.stages, stage] } : t)))
      const response = await streamQuestion(question, currentDialog, onStage)
      if (response.dialog_id) setDialogId(response.dialog_id)
      setTurns((all) => all.map((t) => (t.id === turnId ? { ...t, state: 'done', response } : t)))
    } catch (err) {
      const error = err instanceof Error ? err.message : 'Неизвестная ошибка'
      setTurns((all) => all.map((t) => (t.id === turnId ? { ...t, state: 'error', error } : t)))
    }
  }, [])

  const ask = (question: string) => {
    const text = question.trim()
    if (!text || busy) return
    const id = nextTurnId()
    setTurns((all) => [...all, { id, question: text, state: 'pending', stages: [] }])
    void runTurn(id, text, dialogId)
  }

  const retry = (turnId: string) => {
    const turn = turns.find((t) => t.id === turnId)
    if (!turn || busy) return
    setTurns((all) => all.map((t) => (t.id === turnId ? { ...t, state: 'pending', stages: [], error: undefined } : t)))
    void runTurn(turnId, turn.question, dialogId)
  }

  const newQuestion = useCallback(() => {
    setDialogId(null)
    setTurns([])
    setDrawer(null)
    setPanel(null)
    setSideOpen(false)
  }, [])

  const findDocument = useCallback(() => {
    setSideOpen(false)
    setPanel('registry')
  }, [])

  // N — новый вопрос, / — поиск документа (когда курсор не в поле ввода)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const t = e.target
      if (t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      const key = e.key.toLowerCase()
      if (key === 'n' || key === 'т') {
        e.preventDefault()
        newQuestion()
      } else if (e.key === '/' || e.key === '.') {
        e.preventDefault()
        findDocument()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [newQuestion, findDocument])

  const loadDialog = async (id: string) => {
    setPanel(null)
    setSideOpen(false)
    const dialog = await getDialog(id)
    const loaded: Turn[] = []
    for (let i = 0; i < dialog.messages.length; i++) {
      const msg = dialog.messages[i]
      if (msg.role !== 'user') continue
      const reply = dialog.messages[i + 1]
      loaded.push({
        id: nextTurnId(),
        question: msg.content,
        stages: [],
        state: reply?.response ? 'done' : 'error',
        response: reply?.response,
        error: reply?.response ? undefined : 'Ответ не сохранился.',
      })
    }
    setDialogId(id)
    setTurns(loaded)
    setDrawer(null)
  }

  const openSource = (source: Source, siblings: Source[]) =>
    setDrawer({ docId: source.doc_id, title: source.doc_title, source, siblings })

  const openDocument = (doc: DocumentSummary) => {
    setPanel(null)
    setSideOpen(false)
    setDrawer({ docId: doc.doc_id, title: doc.title, siblings: [] })
  }

  return (
    <div className={`app ${turns.length === 0 ? 'is-start' : ''}`}>
      <Backdrop />
      <Sidebar
        open={sideOpen}
        docs={docs}
        currentDialogId={dialogId}
        activeDocId={drawer?.docId ?? null}
        refreshKey={`${dialogId}:${turns.filter((t) => t.state !== 'pending').length}`}
        onNewQuestion={newQuestion}
        onFindDocument={findDocument}
        onOpenDocument={openDocument}
        onSelectDialog={loadDialog}
        onShowHistory={() => {
          setSideOpen(false)
          setPanel('history')
        }}
        onClose={() => setSideOpen(false)}
      />

      <div className="body">
        {panel && (
          <Panel
            tab={panel}
            docs={docs}
            currentDialogId={dialogId}
            activeDocId={drawer?.docId ?? null}
            onTab={setPanel}
            onSelectDialog={loadDialog}
            onOpenDocument={openDocument}
            onClose={() => setPanel(null)}
          />
        )}

        <main className="main">
          <div className="topbar">
            <button className="icon-btn menu-btn" onClick={() => setSideOpen(true)} aria-label="Открыть меню">
              <Icon name="menu" />
            </button>
            <nav className="crumb" aria-label="Где вы">
              <b>Вопрос</b>
              <span aria-hidden="true">/</span>
              <span className="crumb-cur">{turns.length ? turns[0].question : 'новый'}</span>
            </nav>
            <span className="check-chip" title="Цифры, даты и ссылки в ответе сверяются с текстом документов">
              <i />
              <span className="label-wide">Проверка ответов включена</span>
            </span>
          </div>
          <div className="scroll">
            <div className="page">
            {turns.length === 0 ? (
              <StartScreen docs={docs} busy={busy} onAsk={ask} />
            ) : (
              <div className="thread">
                {turns.map((turn) => (
                  <section key={turn.id} className="turn folio">
                    <h2 className="question">{turn.question}</h2>
                    {turn.state === 'pending' && (
                      <>
                        <article className="sheet glass">
                          <PendingSteps stages={turn.stages} />
                        </article>
                        <aside className="margin" />
                      </>
                    )}
                    {turn.state === 'error' && (
                      <>
                        <article className="sheet glass error-box" role="alert">
                          <Icon name="alert" />
                          <div>
                            <p>Ответ не получен. {turn.error}</p>
                            <button className="line-btn" onClick={() => retry(turn.id)} disabled={busy}>
                              <Icon name="refresh" size={16} /> Спросить ещё раз
                            </button>
                          </div>
                        </article>
                        <aside className="margin" />
                      </>
                    )}
                    {turn.state === 'done' && turn.response && (
                      <AnswerBlock
                        response={turn.response}
                        activeChunkId={drawer?.source?.chunk_id ?? null}
                        onOpenSource={openSource}
                      />
                    )}
                  </section>
                ))}
                <ScrollAnchor trigger={`${turns.length}:${turns[turns.length - 1]?.state}`} />
              </div>
            )}
            </div>
          </div>
          {turns.length > 0 && (
            <div className="dock">
              <div className="dock-inner">
                <Composer busy={busy} placeholder="Уточните или задайте новый вопрос" onSend={ask} autoFocus />
              </div>
            </div>
          )}
        </main>

        {drawer && <DocumentDrawer target={drawer} onNavigate={setDrawer} onClose={() => setDrawer(null)} />}
      </div>
    </div>
  )
}

function ScrollAnchor({ trigger }: { trigger: string }) {
  const [el, setEl] = useState<HTMLDivElement | null>(null)
  useEffect(() => {
    el?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [el, trigger])
  return <div ref={setEl} />
}
