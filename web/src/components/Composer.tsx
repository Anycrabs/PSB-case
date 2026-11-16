import { useEffect, useRef, useState, type KeyboardEvent } from 'react'

interface Props {
  busy: boolean
  placeholder: string
  onSend: (text: string) => void
  autoFocus?: boolean
  /** что делать по Enter и по кнопке, пока поле пустое (на начальном экране — спросить выбранный пример) */
  onEmptySubmit?: (() => void) | null
  /** показать на кнопке клавишу ⏎ */
  showKey?: boolean
}

export function Composer({ busy, placeholder, onSend, autoFocus, onEmptySubmit, showKey }: Props) {
  const [text, setText] = useState('')
  const ref = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }, [text])

  const canSend = !busy && (text.trim() !== '' || Boolean(onEmptySubmit))

  const submit = () => {
    if (busy) return
    if (!text.trim()) {
      onEmptySubmit?.()
      return
    }
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
      <button className="ask-btn" onClick={submit} disabled={!canSend} title={busy ? 'Дождитесь ответа' : undefined}>
        Спросить
        {showKey && <kbd aria-hidden="true">⏎</kbd>}
      </button>
    </div>
  )
}
