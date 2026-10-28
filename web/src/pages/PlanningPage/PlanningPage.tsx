import { useEffect, useRef } from 'react'

import type { TripSession } from '../../features/trip-session/useTripSession'
import { ChatPanel } from '../../widgets/ChatPanel/ChatPanel'
import { PlanningHeader } from '../../widgets/PlanningHeader/PlanningHeader'
import { TripBoard } from '../../widgets/TripBoard/TripBoard'

import styles from './PlanningPage.module.scss'

interface Props {
  session: TripSession
  initialMessage: string | null
  onExit: () => void
}

export function PlanningPage({ session, initialMessage, onExit }: Props) {
  const started = useRef(false)

  useEffect(() => {
    if (initialMessage && !started.current && !session.tripId) {
      started.current = true
      void session.send(initialMessage)
    }
  }, [initialMessage, session])

  return (
    <div className={styles.page}>
      <span className={`${styles.blob} ${styles.blobA}`} aria-hidden="true" />
      <span className={`${styles.blob} ${styles.blobB}`} aria-hidden="true" />
      <PlanningHeader onNewTrip={onExit} />
      <div className={styles.layout}>
        <aside className={styles.chat}>
          <ChatPanel session={session} />
        </aside>
        <main className={styles.board}>
          <TripBoard session={session} />
        </main>
      </div>
    </div>
  )
}
