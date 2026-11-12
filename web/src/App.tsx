import { useCallback, useEffect, useState } from 'react'
import { getDialog, listDocuments, streamQuestion, type PipelineStage } from './api/client'
import type { ChatResponse, DocumentSummary, Source } from './api/types'
import { Composer } from './components/Composer'
import { Icon } from './components/Icon'
import { StartScreen } from './components/StartScreen'
import { documentsWord } from './components/plural'
import { Panel, type PanelTab } from './components/Panel'
import { AnswerBlock } from './components/AnswerBlock'
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

  const newQuestion = () => {
    setDialogId(null)
    setTurns([])
    setDrawer(null)
    setPanel(null)
  }

  const loadDialog = async (id: string) => {
    setPanel(null)
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
    setDrawer({ docId: doc.doc_id, title: doc.title, siblings: [] })
  }

  return (
    <div className="app">
      <header className="header">
        <button className="brand" onClick={newQuestion} title="На главную">
          <span className="brand-mark">
            <Icon name="scale" size={16} />
          </span>
          <span className="brand-title">Юридический ассистент</span>
        </button>
        <button className="ghost-btn only-wide" onClick={() => setPanel('registry')}>
          <Icon name="book" size={16} />
          {docs?.length ? `Реестр: ${docs.length} ${documentsWord(docs.length)}` : 'Реестр'}
        </button>
        <button className="ghost-btn" onClick={() => setPanel('history')}>
          <Icon name="chat" size={16} /> История
        </button>
        {turns.length > 0 && (
          <button className="line-btn" onClick={newQuestion}>
            <Icon name="plus" size={16} /> Новый вопрос
          </button>
        )}
      </header>

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
          <div className="scroll">
            {turns.length === 0 ? (
              <StartScreen docs={docs} busy={busy} onAsk={ask} onOpenRegistry={() => setPanel('registry')} />
            ) : (
              <div className="thread">
                {turns.map((turn) => (
                  <section key={turn.id} className="turn">
                    <div className="question-label">Вопрос</div>
                    <h2 className="question">{turn.question}</h2>
                    {turn.state === 'pending' && <PendingSteps stages={turn.stages} />}
                    {turn.state === 'error' && (
                      <div className="error-box" role="alert">
                        <Icon name="alert" />
                        <div>
                          <p>Ответ не получен. {turn.error}</p>
                          <button className="line-btn" onClick={() => retry(turn.id)}>
                            <Icon name="refresh" size={16} /> Спросить ещё раз
                          </button>
                        </div>
                      </div>
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
          {turns.length > 0 && (
            <div className="dock">
              <Composer busy={busy} placeholder="Уточните или задайте новый вопрос" onSend={ask} autoFocus />
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
