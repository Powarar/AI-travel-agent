import { ruNames } from '../../shared/lib/placeNames'
import type { SSEEvent } from '../../shared/types/contracts'
import { TypingDots } from '../../shared/ui/TypingDots/TypingDots'

import styles from './ChatPanel.module.scss'

const STATUS: Record<string, string> = {
  PARSE: 'Разбираю запрос',
  CLARIFY: 'Уточняю детали',
  INSPIRE: 'Подбираю направления',
  DECIDE: 'Выбираю направление',
  SEARCH: 'Ищу перелёты и отели',
  COMPOSE: 'Собираю поездку',
  VALIDATE: 'Проверяю стыковки и бюджет',
  REPAIR: 'Исправляю план',
  SHORTLIST: 'Сравниваю варианты',
  EXPLAIN: 'Готовлю ответ',
  PLAN: 'Почти готово',
}

export function AgentAvatar() {
  return <span className={styles.avatar} aria-hidden="true">О!</span>
}

/** Реплики агента: вопросы и ответ. Пока ход идёт — одна строка о том, чем он занят. */
export function AgentTurn({ events, thinking }: { events: SSEEvent[]; thinking: boolean }) {
  const messages = events.flatMap((e): { kind: 'question' | 'answer'; text: string }[] => {
    if (e.event === 'question') return [{ kind: 'question', text: ruNames(e.data.text) }]
    if (e.event === 'answer_text') return [{ kind: 'answer', text: ruNames(e.data.text) }]
    return []
  })
  const phase = [...events].reverse().find((e) => e.event === 'phase_changed')
  const status = phase?.event === 'phase_changed' ? STATUS[phase.data.to] : undefined
  if (!messages.length && !thinking) return null
  return (
    <div className={`${styles.agentTurn} motion-fade-up`}>
      <AgentAvatar />
      <div className={styles.agentBody}>
        {messages.map((m, i) => (
          <p key={i} className={`${styles.bubble} ${styles.agentBubble} ${m.kind === 'answer' ? styles.clamped : ''} motion-fade-up`}>
            {m.text}
          </p>
        ))}
        {thinking && (
          <p className={`${styles.bubble} ${styles.agentBubble} ${styles.thinking}`}>
            <TypingDots />
            <span key={status} className={styles.status}>{status ?? 'Думаю'}…</span>
          </p>
        )}
      </div>
    </div>
  )
}
