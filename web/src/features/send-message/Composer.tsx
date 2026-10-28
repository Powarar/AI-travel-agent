import { useState, type CSSProperties, type FormEvent } from 'react'

import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './Composer.module.scss'

interface Props {
  busy: boolean
  locked: boolean
  hasPlan: boolean
  suggestions: string[]
  onSend: (text: string) => void
}

export function Composer({ busy, locked, hasPlan, suggestions, onSend }: Props) {
  const [draft, setDraft] = useState('')
  const disabled = busy || locked

  function submit(event?: FormEvent) {
    event?.preventDefault()
    const text = draft.trim()
    if (!text || disabled) return
    setDraft('')
    onSend(text)
  }

  return (
    <form className={styles.composer} onSubmit={submit}>
      {suggestions.length > 0 && (
        <div className={styles.suggestions} aria-label="Быстрые правки">
          {suggestions.map((s, i) => (
            <button key={s} type="button" className={`${styles.chip} motion-pop`} style={{ '--i': i } as CSSProperties}
              disabled={busy} onClick={() => onSend(s)}>
              <Icon name="refresh" size={14} /> {s}
            </button>
          ))}
        </div>
      )}
      <div className={`${styles.field} ${disabled ? styles.disabled : ''}`.trim()}>
        <textarea
          aria-label={hasPlan ? 'Изменить требования' : 'Сообщение агенту'}
          placeholder={busy ? 'Агент собирает поездку…' : locked ? 'Сначала ответьте на вопрос выше' : hasPlan ? 'Что поменять в поездке?' : 'Куда и как хочется поехать?'}
          rows={1}
          value={draft}
          disabled={disabled}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
        />
        <button type="submit" className={styles.send} disabled={disabled || !draft.trim()} aria-label="Отправить">
          <Icon name="send" size={18} />
        </button>
      </div>
    </form>
  )
}
