import { useState, type CSSProperties, type FormEvent } from 'react'

import { FIELD_LABEL } from '../../shared/lib/labels'
import type { SSEPayloads } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import styles from './QuestionCard.module.scss'

interface Props {
  question: SSEPayloads['question']
  disabled: boolean
  onAnswer: (field: string, value: string, label?: string) => void
}

export function QuestionCard({ question, disabled, onAnswer }: Props) {
  const [value, setValue] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    if (value.trim()) onAnswer(question.field, value.trim())
  }

  return (
    <div className={`${styles.card} motion-pop`} role="group" aria-label="Вопрос агента">
      <div className={styles.head}>
        <span className={styles.kicker}><Icon name="sparkle" size={14} /> {FIELD_LABEL[question.field] ?? question.field}</span>
      </div>
      <div className={styles.chips}>
        {question.options.map((option, i) => (
          <button key={option} type="button" className={`${styles.option} motion-pop`} style={{ '--i': i + 1 } as CSSProperties}
            disabled={disabled} onClick={() => onAnswer(question.field, option)}>
            {option}
          </button>
        ))}
        {question.allow_skip && (
          <button type="button" className={`${styles.option} ${styles.skip}`} disabled={disabled}
            onClick={() => onAnswer(question.field, '', 'Пропустить')}>
            Пропустить
          </button>
        )}
      </div>
      <form className={styles.own} onSubmit={submit}>
        <input aria-label="Свой ответ" placeholder="Или напишите свой вариант" value={value} disabled={disabled}
          onChange={(e) => setValue(e.target.value)} />
        <button type="submit" disabled={disabled || !value.trim()} aria-label="Ответить"><Icon name="arrow" size={16} /></button>
      </form>
    </div>
  )
}
