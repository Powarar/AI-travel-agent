import { useState, type FormEvent } from 'react'

import { FIELD_LABEL } from '../../shared/lib/labels'
import type { SSEPayloads } from '../../shared/types/contracts'
import { PromptChip } from '../../shared/ui/PromptChip/PromptChip'

import styles from './QuestionCard.module.scss'

interface Props {
  question: SSEPayloads['question']
  disabled: boolean
  resumed: boolean
  onAnswer: (field: string, value: string) => void
}

export function QuestionCard({ question, disabled, resumed, onAnswer }: Props) {
  const [value, setValue] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    if (value.trim()) onAnswer(question.field, value.trim())
  }

  return (
    <div className={styles.card} role="group" aria-label="Вопрос">
      <span className={styles.kicker}>
        {FIELD_LABEL[question.field] ?? question.field}
        {resumed && ' · ход продолжен после перезагрузки'}
      </span>
      <p className={styles.text}>{question.text}</p>
      <div className={styles.chips}>
        {question.options.map((option) => (
          <PromptChip key={option} disabled={disabled} onClick={() => onAnswer(question.field, option)}>
            {option}
          </PromptChip>
        ))}
        {question.allow_skip && (
          <PromptChip className={styles.skip} disabled={disabled} onClick={() => onAnswer(question.field, '')}>
            Пропустить
          </PromptChip>
        )}
      </div>
      <form className={styles.own} onSubmit={submit}>
        <input
          aria-label="Свой ответ"
          placeholder="Или свой ответ"
          value={value}
          disabled={disabled}
          onChange={(e) => setValue(e.target.value)}
        />
        <button type="submit" disabled={disabled || !value.trim()}>Ответить</button>
      </form>
    </div>
  )
}
