import type { IconName } from '../../shared/ui/Icon/Icon'
import { PHASE_LABEL, TOOL_LABEL } from '../../shared/lib/labels'
import type { SSEEvent } from '../../shared/types/contracts'

export interface ActivityRow {
  icon: IconName
  text: string
  meta?: string
  tone?: 'ok' | 'warn' | 'bad' | 'accent'
}

const KIND: Record<string, string> = { destination: 'направления', hotel: 'отели' }

/** Строка журнала для события процесса; `null` — событие показывается не здесь. */
export function activityRow(event: SSEEvent): ActivityRow | null {
  switch (event.event) {
    case 'phase_changed':
      return { icon: 'arrow', text: PHASE_LABEL[event.data.to] ?? event.data.to }
    case 'tool_completed':
      return {
        icon: 'check',
        text: `${TOOL_LABEL[event.data.tool] ?? event.data.tool}: ${event.data.count} в выдаче`,
        meta: `${event.data.ms} мс`,
        tone: event.data.status === 'ok' ? 'ok' : 'warn',
      }
    case 'tool_failed':
      return { icon: 'alert', text: `${TOOL_LABEL[event.data.tool] ?? event.data.tool}: отказ`, meta: `попытка ${event.data.attempt}`, tone: 'warn' }
    case 'llm_call':
      return { icon: 'sparkle', text: `модель · ${event.data.slot}`, meta: `${event.data.ms} мс` }
    case 'candidates':
      return { icon: 'sparkle', text: `Отобраны ${KIND[event.data.kind] ?? event.data.kind}: ${event.data.items.length}`, tone: 'accent' }
    case 'violation':
      return { icon: 'alert', text: event.data.code, meta: event.data.affected_component, tone: event.data.severity === 'blocking' ? 'bad' : 'warn' }
    case 'repair_started':
      return { icon: 'refresh', text: `Исправляем ${event.data.violation_code}`, meta: `итерация ${event.data.iteration}` }
    case 'plan_updated':
      return { icon: 'star', text: `План v${event.data.version}`, meta: `изменено ${event.data.changed_components.length} · сохранено ${event.data.kept_components.length}`, tone: 'ok' }
    case 'budget_exhausted':
      return { icon: 'clock', text: `Лимит хода: ${event.data.limit_hit}`, tone: 'warn' }
    default:
      return null
  }
}
