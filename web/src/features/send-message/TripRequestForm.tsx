import { useState, type FormEvent } from 'react'

import { Button } from '../../shared/ui/Button/Button'
import { Card } from '../../shared/ui/Card/Card'
import { PromptChip } from '../../shared/ui/PromptChip/PromptChip'
import { TextareaField } from '../../shared/ui/TextareaField/TextareaField'

import styles from './TripRequestForm.module.scss'

const suggestions = [
  'На выходные к морю из Москвы, до 45 000 ₽',
  'Неделя в горах для двоих в ноябре',
  'Хочу тёплое место без визы и с хорошим отелем',
]

export function TripRequestForm() {
  const [prompt, setPrompt] = useState('')
  const [submittedPrompt, setSubmittedPrompt] = useState('')

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const value = prompt.trim()
    if (value) setSubmittedPrompt(value)
  }

  return (
    <Card as="section" className={styles.card} aria-label="Запрос на поездку">
      <form onSubmit={handleSubmit}>
        <TextareaField
          label="Расскажите о поездке"
          placeholder="Например: хотим на 5 дней к морю в ноябре, нас двое, бюджет до 120 000 ₽..."
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          hint="Можно указать даты, бюджет, компанию и то, что важно в поездке."
          rows={4}
        />
        <div id="examples" className={styles.suggestions}>
          <p>Или начните с примера</p>
          <div className={styles.suggestionsList}>
            {suggestions.map((suggestion) => (
              <PromptChip key={suggestion} onClick={() => setPrompt(suggestion)}>
                {suggestion}
              </PromptChip>
            ))}
          </div>
        </div>
        <div className={styles.action}>
          <Button type="submit" disabled={!prompt.trim()}>
            Подготовить запрос <span aria-hidden="true">→</span>
          </Button>
        </div>
      </form>
      {submittedPrompt && (
        <div className={styles.preview} role="status" aria-live="polite">
          <strong>Запрос подготовлен</strong>
          <p>{submittedPrompt}</p>
          <small>Подбор вариантов появится после подключения API планирования.</small>
        </div>
      )}
    </Card>
  )
}
