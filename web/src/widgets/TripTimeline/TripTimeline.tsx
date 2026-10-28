import type { CSSProperties } from 'react'

import { COMPONENT_LABEL } from '../../shared/lib/labels'
import { componentCandidate, plural } from '../../shared/lib/plan'
import type { Candidate, TripState } from '../../shared/types/contracts'
import { Icon, type IconName } from '../../shared/ui/Icon/Icon'
import { StatusBadge } from '../../shared/ui/StatusBadge/StatusBadge'

import styles from './TripTimeline.module.scss'

const STEPS: { component: string; icon: IconName }[] = [
  { component: 'transport_in', icon: 'plane' },
  { component: 'transfer_arrival', icon: 'car' },
  { component: 'hotel', icon: 'hotel' },
  { component: 'transfer_departure', icon: 'car' },
  { component: 'transport_out', icon: 'plane' },
]

function day(iso?: string) {
  return iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}` : ''
}

function time(iso?: string) {
  return iso ? iso.slice(11, 16) : ''
}

function details(component: string, c: Candidate, state: TripState): { title: string; lines: string[] } {
  const r = c.ref
  if (component.startsWith('transport')) {
    return {
      title: `${time(r.dep_at)} → ${time(r.arr_at)}`,
      lines: [`${day(r.dep_at)} · ${r.duration_h} ч в пути`, r.direct === 'true' ? 'прямой' : 'с пересадкой', r.overnight === 'true' ? 'ночной' : ''].filter(Boolean),
    }
  }
  if (component === 'hotel') {
    const nights = state.request.nights?.value
    return {
      title: r.name ?? c.candidate_id,
      lines: [
        r.stars ? '★'.repeat(Number(r.stars)) : '',
        nights ? `${nights} ${plural(nights, 'ночь', 'ночи', 'ночей')}` : '',
        r.check_in_deadline ? `заселение до ${r.check_in_deadline}` : '',
        r.pet_friendly === 'true' ? 'можно с питомцем' : '',
      ].filter(Boolean),
    }
  }
  return { title: r.minutes ? `${r.minutes} мин в пути` : c.candidate_id, lines: [r.kind === 'arrival' ? 'из аэропорта в отель' : 'из отеля в аэропорт'] }
}

/** Маршрут поездки по шагам корзины: перелёт → трансфер → отель → трансфер → перелёт. */
export function TripTimeline({ state }: { state: TripState }) {
  const steps = STEPS.map((s) => ({ ...s, c: componentCandidate(state, s.component) })).filter((s) => s.c)
  if (!steps.length) return null
  return (
    <section className={styles.card} aria-labelledby="timeline-title">
      <h3 id="timeline-title" className={styles.title}>Маршрут</h3>
      <ol className={styles.timeline}>
        <span className={styles.line} aria-hidden="true" />
        {steps.map(({ component, icon, c }, i) => {
          const d = details(component, c!, state)
          return (
            <li key={component} className={`${styles.step} motion-fade-up`} style={{ '--i': i } as CSSProperties}>
              <span className={`${styles.node} ${component === 'hotel' ? styles.hotel : ''}`.trim()}><Icon name={icon} size={18} /></span>
              <div className={styles.body}>
                <span className={styles.kicker}>{COMPONENT_LABEL[component]}</span>
                <strong>{d.title}</strong>
                <span className={styles.lines}>{d.lines.join(' · ')}</span>
              </div>
              <StatusBadge status={c!.data_status} />
            </li>
          )
        })}
      </ol>
    </section>
  )
}
