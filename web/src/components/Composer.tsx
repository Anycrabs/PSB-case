import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Icon } from './Icon'

interface Props {
  busy: boolean
  onSend: (text: string) => void
}

export function Composer({ busy, onSend }: Props) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [text])

  const submit = () => {
    if (!text.trim() || busy) return
    onSend(text)
    setText('')
  }

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      submit()
    }
  }

  return (
    <div className="composer-wrap">
      <div className="composer">
        <textarea
          ref={ref}
          rows={1}
          value={text}
          placeholder="Задайте вопрос по договорам, регламентам или законам"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          autoFocus
        />
        <button
          className="send-btn"
          onClick={submit}
          disabled={!text.trim() || busy}
          aria-label="Отправить"
          title={busy ? 'Дождитесь ответа' : 'Отправить (Enter)'}
        >
          <Icon name="send" />
        </button>
      </div>
      <p className="composer-hint">Enter — отправить, Shift + Enter — новая строка</p>
    </div>
  )
}
