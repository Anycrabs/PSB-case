import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

interface Props {
  busy: boolean
  placeholder: string
  onSend: (text: string) => void
  autoFocus?: boolean
}

export function Composer({ busy, placeholder, onSend, autoFocus }: Props) {
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
    <div className="composer">
      <textarea
        ref={ref}
        rows={1}
        value={text}
        placeholder={placeholder}
        aria-label="Вопрос"
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
        autoFocus={autoFocus}
      />
      <button
        className="ask-btn"
        onClick={submit}
        disabled={!text.trim() || busy}
        title={busy ? 'Дождитесь ответа' : 'Enter — спросить, Shift + Enter — новая строка'}
      >
        Спросить
      </button>
    </div>
  )
}
