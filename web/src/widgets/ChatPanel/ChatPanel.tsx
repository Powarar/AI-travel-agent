import { useEffect, useMemo, useRef } from 'react'

import { QuestionCard } from '../../features/answer-question/QuestionCard'
import { Composer } from '../../features/send-message/Composer'
import type { FeedItem, TripSession } from '../../features/trip-session/useTripSession'
import type { SSEEvent } from '../../shared/types/contracts'
import { Icon } from '../../shared/ui/Icon/Icon'

import { AgentAvatar, AgentTurn } from './AgentTurn'
import styles from './ChatPanel.module.scss'

type Block = { kind: 'user'; id: number; text: string } | { kind: 'agent'; id: number; events: SSEEvent[] }

/** Лента в блоки: реплики пользователя рвут подряд идущие события агента. */
function toBlocks(feed: FeedItem[]): Block[] {
  const blocks: Block[] = []
  for (const item of feed) {
    if (item.user !== undefined) {
      blocks.push({ kind: 'user', id: item.id, text: item.user })
      continue
    }
    if (item.event.event === 'run_started') continue
    const last = blocks[blocks.length - 1]
    if (last?.kind === 'agent') last.events.push(item.event)
    else blocks.push({ kind: 'agent', id: item.id, events: [item.event] })
  }
  return blocks
}

export function ChatPanel({ session }: { session: TripSession }) {
  const { feed, busy, question, state, error } = session
  const scroller = useRef<HTMLDivElement>(null)
  const blocks = useMemo(() => toBlocks(feed), [feed])
  const waiting = state?.run_status === 'interrupted'
  const hasPlan = Boolean(state && Object.keys(state.chosen.component_refs).length)
  const lastAgent = [...blocks].reverse().find((b) => b.kind === 'agent')
  const tail = blocks[blocks.length - 1]

  useEffect(() => {
    const el = scroller.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' })
  }, [feed.length, busy, question])

  return (
    <section className={styles.panel} aria-label="Диалог с агентом">
      <div className={styles.scroller} ref={scroller}>
        {!blocks.length && (
          <div className={styles.welcome}>
            <AgentAvatar />
            <p className={`${styles.bubble} ${styles.agentBubble}`}>
              Привет! Я соберу поездку под ключ: направление, перелёт, отель и трансфер — с ценой до рубля.
            </p>
          </div>
        )}
        {blocks.map((block) => block.kind === 'user' ? (
          <p key={block.id} className={`${styles.bubble} ${styles.userBubble} motion-fade-up`}>{block.text}</p>
        ) : (
          <AgentTurn
            key={block.id}
            events={block.events}
            live={busy && block === lastAgent && tail === block}
            thinking={busy && block === tail}
          />
        ))}
        {busy && tail?.kind === 'user' && <AgentTurn events={[]} live thinking />}
        {question && waiting && !busy && (
          <QuestionCard question={question} disabled={busy} resumed={session.resumed} onAnswer={session.answer} />
        )}
        {error && (
          <p className={styles.error} role="alert"><Icon name="alert" size={16} /> {error}</p>
        )}
      </div>
      <Composer
        busy={busy}
        locked={waiting}
        hasPlan={hasPlan}
        suggestions={hasPlan && !waiting ? session.suggestions : []}
        onSend={session.send}
      />
    </section>
  )
}
