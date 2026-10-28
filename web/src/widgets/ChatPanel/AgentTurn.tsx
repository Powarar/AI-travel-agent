import type { SSEEvent } from '../../shared/types/contracts'
import { TypingDots } from '../../shared/ui/TypingDots/TypingDots'

import { ActivityLog } from './ActivityLog'
import styles from './ChatPanel.module.scss'

export function AgentAvatar() {
  return <span className={styles.avatar} aria-hidden="true">О!</span>
}

/** Блок агента: журнал действий + его реплики (вопрос, объяснение). */
export function AgentTurn({ events, live, thinking }: { events: SSEEvent[]; live: boolean; thinking: boolean }) {
  const messages = events.flatMap((e): { kind: 'question' | 'answer'; text: string }[] => {
    if (e.event === 'question') return [{ kind: 'question', text: e.data.text }]
    if (e.event === 'answer_text') return [{ kind: 'answer', text: e.data.text }]
    return []
  })
  return (
    <div className={`${styles.agentTurn} motion-fade-up`}>
      <AgentAvatar />
      <div className={styles.agentBody}>
        <ActivityLog events={events} live={live} />
        {messages.map((m, i) => (
          <p key={i} className={`${styles.bubble} ${styles.agentBubble} ${m.kind === 'answer' ? styles.clamped : ''} motion-fade-up`}>
            {m.text}
            {m.kind === 'answer' && <span className={styles.more}>Детали — на доске справа →</span>}
          </p>
        ))}
        {thinking && (
          <p className={`${styles.bubble} ${styles.agentBubble} ${styles.thinking}`}><TypingDots /></p>
        )}
      </div>
    </div>
  )
}
