import { CheckoutPanel } from '../../features/checkout/CheckoutPanel'
import type { TripSession } from '../../features/trip-session/useTripSession'
import { Skeleton } from '../../shared/ui/Skeleton/Skeleton'
import { FinalistCards } from '../FinalistCards/FinalistCards'
import { PhaseStepper } from '../PhaseStepper/PhaseStepper'
import { PlanDiff } from '../PlanDiff/PlanDiff'
import { PriceBreakdown } from '../PriceBreakdown/PriceBreakdown'
import { TripAlerts } from '../TripAlerts/TripAlerts'
import { TripSummary } from '../TripSummary/TripSummary'
import { TripTimeline } from '../TripTimeline/TripTimeline'

import styles from './TripBoard.module.scss'

function EmptyBoard() {
  return (
    <div className={styles.empty}>
      <div className={styles.orb} aria-hidden="true"><span>О!</span></div>
      <h2>Здесь соберётся ваша поездка</h2>
      <p>Направления с погодой, перелёт, отель и трансферы — и честная цена по строкам, без «примерно».</p>
    </div>
  )
}

function BoardSkeleton() {
  return (
    <div className={styles.skeleton} aria-label="Собираем план" role="status">
      <Skeleton height={220} radius={28} />
      <div className={styles.skeletonRow}>
        <Skeleton height={190} radius={24} />
        <Skeleton height={190} radius={24} />
        <Skeleton height={190} radius={24} />
      </div>
      <Skeleton height={260} radius={28} />
    </div>
  )
}

/** Правая часть экрана: всё, что агент собрал, — из TripState и событий текущего хода. */
export function TripBoard({ session }: { session: TripSession }) {
  const { state, busy, phase, previous, planUpdate, order } = session
  const explanationEvent = [...session.feed].reverse().find((i) => i.event?.event === 'answer_text')?.event
  const explanation = explanationEvent?.event === 'answer_text' ? explanationEvent.data : null
  const hasPlan = Boolean(state && Object.keys(state.chosen.component_refs).length)
  const done = Boolean(state && !busy && state.run_status !== 'interrupted' && state.outcome)
  const started = session.feed.length > 0

  return (
    <div className={styles.board}>
      {started && <PhaseStepper phase={phase} busy={busy} done={done} />}
      {!started && <EmptyBoard />}
      {started && !hasPlan && busy && <BoardSkeleton />}
      {state && (hasPlan || (state.outcome && state.outcome !== 'awaiting_answer')) && (
        <div className={`${styles.stack} ${busy ? styles.updating : ''}`.trim()} aria-busy={busy}>
          <TripSummary key={`summary-${state.version}`} state={state} />
          <TripAlerts state={state} explanation={done ? explanation : null} />
          {previous && planUpdate && hasPlan && (
            <PlanDiff key={`diff-${state.version}`} previous={previous} current={state} update={planUpdate} />
          )}
          <FinalistCards key={`fin-${state.version}`} state={state} />
          {hasPlan && (
            <div className={styles.split}>
              <TripTimeline key={`tl-${state.version}`} state={state} />
              <PriceBreakdown key={`pb-${state.version}`} state={state} />
            </div>
          )}
          {hasPlan && <CheckoutPanel state={state} order={order} busy={busy || session.ordering} onCheckout={session.checkout} />}
        </div>
      )}
      {state?.run_status === 'interrupted' && !hasPlan && (
        <div className={styles.waiting}>
          <span className={styles.waitingOrb} aria-hidden="true">?</span>
          <p>Агенту не хватает одной детали — ответьте на вопрос в чате, и поиск продолжится.</p>
        </div>
      )}
    </div>
  )
}
