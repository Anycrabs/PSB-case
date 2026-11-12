import { useCallback, useState } from 'react'
import { getDialog, sendQuestion } from './api/client'
import type { Source } from './api/types'
import { Header } from './components/Header'
import { DialogMenu } from './components/DialogMenu'
import { ChatView, type Turn } from './components/ChatView'
import { Composer } from './components/Composer'
import { DocumentDrawer } from './components/DocumentDrawer'

let turnSeq = 0
const nextTurnId = () => `t${++turnSeq}`

export default function App() {
  const [dialogId, setDialogId] = useState<string | null>(null)
  const [turns, setTurns] = useState<Turn[]>([])
  const [openSource, setOpenSource] = useState<Source | null>(null)
  const [menuOpen, setMenuOpen] = useState(false)

  const busy = turns.some((t) => t.state === 'pending')

  const runTurn = useCallback(async (turnId: string, question: string, currentDialog: string | null) => {
    try {
      const response = await sendQuestion(question, currentDialog)
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
    setTurns((all) => [...all, { id, question: text, state: 'pending' }])
    void runTurn(id, text, dialogId)
  }

  const retry = (turnId: string) => {
    const turn = turns.find((t) => t.id === turnId)
    if (!turn || busy) return
    setTurns((all) => all.map((t) => (t.id === turnId ? { ...t, state: 'pending', error: undefined } : t)))
    void runTurn(turnId, turn.question, dialogId)
  }

  const newDialog = () => {
    setDialogId(null)
    setTurns([])
    setOpenSource(null)
    setMenuOpen(false)
  }

  const loadDialog = async (id: string) => {
    setMenuOpen(false)
    const dialog = await getDialog(id)
    const loaded: Turn[] = []
    for (let i = 0; i < dialog.messages.length; i++) {
      const msg = dialog.messages[i]
      if (msg.role !== 'user') continue
      const reply = dialog.messages[i + 1]
      loaded.push({
        id: nextTurnId(),
        question: msg.content,
        state: reply?.response ? 'done' : 'error',
        response: reply?.response,
        error: reply?.response ? undefined : 'Ответ не сохранился',
      })
    }
    setDialogId(id)
    setTurns(loaded)
    setOpenSource(null)
  }

  return (
    <div className="app">
      <Header onMenu={() => setMenuOpen((v) => !v)} onNewDialog={newDialog} />
      <div className="workspace">
        <DialogMenu
          open={menuOpen}
          currentId={dialogId}
          onSelect={loadDialog}
          onNewDialog={newDialog}
          onClose={() => setMenuOpen(false)}
        />
        <main className="chat-column">
          <ChatView
            turns={turns}
            activeChunkId={openSource?.chunk_id ?? null}
            onAsk={ask}
            onRetry={retry}
            onOpenSource={setOpenSource}
          />
          <Composer busy={busy} onSend={ask} />
        </main>
        {openSource && <DocumentDrawer source={openSource} onClose={() => setOpenSource(null)} />}
      </div>
    </div>
  )
}
