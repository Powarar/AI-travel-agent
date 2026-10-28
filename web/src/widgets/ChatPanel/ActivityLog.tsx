import type { CSSProperties } from 'react'

import type { SSEEvent } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import { activityRow } from './activity'
import styles from './ChatPanel.module.scss'

/** Журнал хода: что агент сделал. Свёрнут у завершённых ходов, раскрыт у текущего. */
export function ActivityLog({ events, live }: { events: SSEEvent[]; live: boolean }) {
  const rows = events.map(activityRow).filter((r) => r !== null)
  if (!rows.length) return null
  const tools = events.filter((e) => e.event === 'tool_completed').length
  const ms = events.reduce((sum, e) => sum + (e.event === 'tool_completed' ? e.data.ms : 0), 0)
  return (
    <details className={styles.activity} open={live}>
      <summary>
        <span className={styles.activityIcon}><Icon name="bolt" size={14} /></span>
        {live ? 'Агент работает' : 'Что сделал агент'}
        <span className={styles.activityMeta}>
          {rows.length} шагов{tools ? ` · ${tools} инструментов · ${ms} мс` : ''}
        </span>
      </summary>
      <ol>
        {rows.map((row, i) => (
          <li key={i} className={`motion-fade-up ${row.tone ? styles[row.tone] : ''}`.trim()} style={{ '--i': Math.min(i, 6) } as CSSProperties}>
            <Icon name={row.icon} size={14} />
            <span>{row.text}</span>
            {row.meta && <small>{row.meta}</small>}
          </li>
        ))}
      </ol>
    </details>
  )
}
