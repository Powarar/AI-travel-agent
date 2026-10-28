import type { FeedItem } from '../../features/trip-session/useTripSession'
import { PHASE_LABEL, TOOL_LABEL } from '../../shared/lib/labels'
import type { SSEEvent } from '../../shared/types/contracts'

import styles from './TripFeed.module.scss'

function describe(event: SSEEvent): { icon: string; text: string; tone?: 'warn' | 'bad' | 'ok' } | null {
  switch (event.event) {
    case 'run_started':
      return { icon: '▶', text: 'Ход начат' }
    case 'phase_changed':
      return { icon: '→', text: PHASE_LABEL[event.data.to] ?? event.data.to }
    case 'tool_started':
      return null
    case 'tool_completed':
      return { icon: '✓', text: `${TOOL_LABEL[event.data.tool] ?? event.data.tool}: ${event.data.count} в выдаче, ${event.data.ms} мс`, tone: 'ok' }
    case 'tool_failed':
      return { icon: '!', text: `${TOOL_LABEL[event.data.tool] ?? event.data.tool}: отказ, попытка ${event.data.attempt}${event.data.will_retry ? ', повторим' : ''}`, tone: 'warn' }
    case 'llm_call':
      return { icon: '◆', text: `модель, слот ${event.data.slot}: ${event.data.ms} мс` }
    case 'question':
      return { icon: '?', text: event.data.text }
    case 'candidates':
      return { icon: '≡', text: `кандидаты (${event.data.kind}): ${event.data.items.length}` }
    case 'violation':
      return { icon: '⚠', text: `${event.data.code}: ждали ${event.data.expected ?? '—'}, вышло ${event.data.actual ?? '—'}`, tone: event.data.severity === 'blocking' ? 'bad' : 'warn' }
    case 'repair_started':
      return { icon: '↻', text: `исправляем ${event.data.violation_code}, итерация ${event.data.iteration}` }
    case 'plan_updated':
      return { icon: '★', text: `план v${event.data.version}: изменено ${event.data.changed_components.length}, сохранено ${event.data.kept_components.length}`, tone: 'ok' }
    case 'answer_text':
      return null
    case 'checkout_completed':
      return { icon: '✓', text: `заказ ${event.data.order_id} (симуляция)`, tone: 'ok' }
    case 'budget_exhausted':
      return { icon: '⏱', text: `лимит хода: ${event.data.limit_hit}`, tone: 'warn' }
  }
}

export function TripFeed({ items, busy }: { items: FeedItem[]; busy: boolean }) {
  const rows = items
    .map((item) => ({ id: item.id, user: item.user, view: item.event ? describe(item.event) : null }))
    .filter((r) => r.view || r.user)
  return (
    <ol className={styles.feed} aria-live="polite" aria-label="Ход подбора">
      {rows.map(({ id, user, view }) => user ? (
        <li key={id} className={styles.user}>{user}</li>
      ) : (
        <li key={id} className={view!.tone ? styles[view!.tone] : undefined}>
          <span className={styles.icon} aria-hidden="true">{view!.icon}</span>
          <span>{view!.text}</span>
        </li>
      ))}
      {busy && <li className={styles.pending}><span className={styles.icon}>…</span><span>думаем</span></li>}
    </ol>
  )
}
