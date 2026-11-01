import { TripRequestForm } from '../../../features/send-message/TripRequestForm'
import type { TripSession } from '../../../features/trip-session/useTripSession'
import { Icon } from '../../../shared/ui/Icon/Icon'
import { ChatPanel } from '../../../widgets/ChatPanel/ChatPanel'
import { TripBoard } from '../../../widgets/TripBoard/TripBoard'

import styles from './Screens.module.scss'

/** Подбор поездки: до первого сообщения — форма запроса, дальше — чат с агентом и план. */
export function PlanScreen({ session }: { session: TripSession }) {
  const started = session.tripId !== null || session.feed.length > 0

  if (!started) {
    return (
      <>
        <header className={styles.head}>
          <span className={styles.kicker}>О! Куда</span>
          <h1>Куда поедем?</h1>
          <p>Опишите поездку своими словами — агент подберёт направление, перелёт и отель.</p>
        </header>
        <TripRequestForm onSubmit={(prompt) => void session.send(prompt)} />
      </>
    )
  }

  return (
    <>
      <header className={styles.bar}>
        <h1>Подбор</h1>
        <button type="button" className={styles.pill} onClick={session.reset} disabled={session.busy}>
          <Icon name="refresh" size={16} /> Новая поездка
        </button>
      </header>
      <div className={styles.chat}>
        <ChatPanel session={session} />
      </div>
      <TripBoard session={session} />
    </>
  )
}
