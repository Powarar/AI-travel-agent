import { useEffect, useRef, useState, type FormEvent } from 'react'

import { QuestionCard } from '../../features/answer-question/QuestionCard'
import { CheckoutPanel } from '../../features/checkout/CheckoutPanel'
import type { TripSession } from '../../features/trip-session/useTripSession'
import { Container } from '../../shared/ui/Container/Container'
import { PromptChip } from '../../shared/ui/PromptChip/PromptChip'
import { FinalistCards } from '../../widgets/FinalistCards/FinalistCards'
import { PlanDiff } from '../../widgets/PlanDiff/PlanDiff'
import { PlanLedger } from '../../widgets/PlanLedger/PlanLedger'
import { SiteHeader } from '../../widgets/SiteHeader/SiteHeader'
import { TripFeed } from '../../widgets/TripFeed/TripFeed'

import styles from './PlanningPage.module.scss'

const CHANGES = ['Давай лучше в Сочи, перелёт покороче', 'Нас будет трое', 'У нас собака']

interface Props {
  session: TripSession
  initialMessage: string | null
  onExit: () => void
}

export function PlanningPage({ session, initialMessage, onExit }: Props) {
  const { state, feed, question, busy, error } = session
  const [draft, setDraft] = useState('')
  const started = useRef(false)
  const feedRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (initialMessage && !started.current && !session.tripId) {
      started.current = true
      void session.send(initialMessage)
    }
  }, [initialMessage, session])

  useEffect(() => {
    const el = feedRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [feed.length, busy])

  const explanation = [...feed].reverse().find((i) => i.event?.event === 'answer_text')?.event
  const streamed = session.streamCandidates.find((c) => c.kind === 'destination')?.items ?? []
  const hasPlan = Boolean(state && Object.keys(state.chosen.component_refs).length)
  const waiting = state?.run_status === 'interrupted'

  function submit(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text) return
    setDraft('')
    void session.send(text)
  }

  return (
    <div className={styles.page}>
      <SiteHeader />
      <Container className={styles.layout}>
        <aside className={styles.chat} aria-label="Диалог">
          <div className={styles.chatHead}>
            <h2>Подбор поездки</h2>
            <button type="button" className={styles.link} onClick={onExit}>Новая поездка</button>
          </div>
          {session.mode === 'fixture' && (
            <p className={styles.mode}>Офлайн-режим: ответы агента проигрываются из записанного прогона на каталоге.</p>
          )}
          <div className={styles.feed} ref={feedRef}>
            <TripFeed items={feed} busy={busy} />
          </div>
          {question && waiting && (
            <QuestionCard question={question} disabled={busy} resumed={session.resumed} onAnswer={session.answer} />
          )}
          {error && <p className={styles.error} role="alert">{error}</p>}
          <form className={styles.composer} onSubmit={submit}>
            {hasPlan && !waiting && (
              <div className={styles.changes}>
                {CHANGES.map((c) => <PromptChip key={c} disabled={busy} onClick={() => session.send(c)}>{c}</PromptChip>)}
              </div>
            )}
            <div className={styles.inputRow}>
              <textarea
                aria-label={hasPlan ? 'Изменить требования' : 'Сообщение'}
                placeholder={hasPlan ? 'Что поменять в плане?' : 'Опишите поездку'}
                rows={2}
                value={draft}
                disabled={busy || waiting}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    e.currentTarget.form?.requestSubmit()
                  }
                }}
              />
              <button type="submit" disabled={busy || waiting || !draft.trim()} aria-label="Отправить">→</button>
            </div>
          </form>
        </aside>

        <main className={styles.results}>
          {!state && <p className={styles.placeholder}>Здесь появятся направления, план и разбивка цены.</p>}
          <FinalistCards state={state} streamed={streamed} />
          {state && hasPlan && (
            <PlanLedger
              state={state}
              explanation={explanation?.event === 'answer_text' ? explanation.data : null}
            />
          )}
          {state && !hasPlan && state.outcome && state.outcome !== 'awaiting_answer' && (
            <PlanLedger state={state} explanation={explanation?.event === 'answer_text' ? explanation.data : null} />
          )}
          {state && session.previous && session.planUpdate && hasPlan && (
            <PlanDiff previous={session.previous} current={state} update={session.planUpdate} />
          )}
          {state && hasPlan && (
            <CheckoutPanel state={state} order={session.order} busy={busy} onCheckout={session.checkout} />
          )}
        </main>
      </Container>
    </div>
  )
}
