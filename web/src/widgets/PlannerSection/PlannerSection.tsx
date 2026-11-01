import { TripRequestForm } from '../../features/send-message/TripRequestForm'
import { Container } from '../../shared/ui/Container/Container'

import styles from './PlannerSection.module.scss'

export function PlannerSection({ onStart }: { onStart: (prompt: string) => void }) {
  return (
    <section id="planner" className={styles.section} aria-labelledby="planner-title">
      <Container className={styles.layout}>
        <div className={styles.copy}>
          <span className={styles.kicker}>О! Куда</span>
          <h2 id="planner-title">С чего начнём?</h2>
          <p>Не нужен подробный маршрут. Опишите поездку своими словами — даже если пока знаете только настроение.</p>
          <div className={styles.accent} aria-hidden="true">О!</div>
        </div>
        <TripRequestForm onSubmit={onStart} />
      </Container>
    </section>
  )
}
