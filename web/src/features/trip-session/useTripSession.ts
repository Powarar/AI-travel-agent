import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../../shared/api'
import type { CheckoutResult, SSEEvent, SSEPayloads, TripState } from '../../shared/types/contracts'

type Question = SSEPayloads['question']
type PlanUpdate = SSEPayloads['plan_updated']

/** Элемент ленты: событие агента или реплика пользователя; `turn` — номер хода для группировки. */
export type FeedItem =
  | { id: number; turn: number; event: SSEEvent; user?: undefined }
  | { id: number; turn: number; user: string; event?: undefined }

interface Persisted {
  tripId: string
  runId: string | null
  seen: number
  previous: TripState | null
}

const KEY = 'okuda.session'

function readPersisted(): Persisted | null {
  try {
    const raw = localStorage.getItem(KEY)
    return raw ? (JSON.parse(raw) as Persisted) : null
  } catch {
    return null
  }
}

function writePersisted(value: Persisted | null) {
  try {
    if (value) localStorage.setItem(KEY, JSON.stringify(value))
    else localStorage.removeItem(KEY)
  } catch {
    // без хранилища сессия не переживёт перезагрузку, но работает
  }
}

function lastQuestion(events: SSEEvent[], awaiting: string[]): Question | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i]
    if (e.event === 'question' && awaiting.includes(e.data.field)) return e.data
  }
  return null
}

export function useTripSession() {
  const [tripId, setTripId] = useState<string | null>(null)
  const [state, setState] = useState<TripState | null>(null)
  const [previous, setPrevious] = useState<TripState | null>(null)
  const [feed, setFeed] = useState<FeedItem[]>([])
  const [question, setQuestion] = useState<Question | null>(null)
  const [planUpdate, setPlanUpdate] = useState<PlanUpdate | null>(null)
  const [phase, setPhase] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [ordering, setOrdering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [order, setOrder] = useState<CheckoutResult | null>(null)
  const [resumed, setResumed] = useState(false)
  const [suggestions, setSuggestions] = useState<string[]>([])
  const run = useRef<{ runId: string | null; seen: number }>({ runId: null, seen: 0 })
  const counter = useRef(0)
  const turn = useRef(0)

  const persist = useCallback((trip: string, prev: TripState | null) => {
    writePersisted({ tripId: trip, runId: run.current.runId, seen: run.current.seen, previous: prev })
  }, [])

  const apply = useCallback((event: SSEEvent) => {
    const id = (counter.current += 1)
    const at = turn.current
    setFeed((items) => [...items, { id, turn: at, event }])
    switch (event.event) {
      case 'run_started':
        run.current = { runId: event.data.run_id, seen: 0 }
        break
      case 'phase_changed':
        setPhase(event.data.to)
        break
      case 'question':
        setQuestion(event.data)
        break
      case 'plan_updated':
        setPlanUpdate(event.data)
        break
      default:
        break
    }
    run.current.seen += 1
  }, [])

  // Числа на экране берутся из TripState после хода, а не из текста событий.
  const refresh = useCallback(async (trip: string, prev: TripState | null) => {
    const next = await api.getState(trip)
    setState(next)
    setPhase(next.stage)
    if (next.run_status !== 'interrupted') setQuestion(null)
    setSuggestions(api.suggestions())
    persist(trip, prev)
    return next
  }, [persist])

  useEffect(() => {
    const saved = readPersisted()
    if (!saved) return
    let cancelled = false
    ;(async () => {
      try {
        const current = await api.getState(saved.tripId)
        if (cancelled) return
        setTripId(saved.tripId)
        setState(current)
        setPhase(current.stage)
        setPrevious(saved.previous)
        setSuggestions(api.suggestions())
        run.current = { runId: saved.runId, seen: saved.seen }
        if (saved.runId) {
          const trace = await api.getRun(saved.runId)
          if (cancelled) return
          counter.current = trace.events.length
          turn.current = 1
          setFeed(trace.events.map((event, i) => ({ id: i + 1, turn: 1, event })))
          run.current.seen = trace.events.length
          const update = [...trace.events].reverse().find((e) => e.event === 'plan_updated')
          if (update?.event === 'plan_updated') setPlanUpdate(update.data)
          if (current.run_status === 'interrupted') {
            setQuestion(lastQuestion(trace.events, current.awaiting_fields))
            setResumed(true)
          }
        }
      } catch {
        writePersisted(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const send = useCallback(async (text: string) => {
    setBusy(true)
    setError(null)
    setOrder(null)
    setResumed(false)
    try {
      let trip = tripId
      if (!trip) {
        trip = await api.createTrip()
        setTripId(trip)
      }
      // Новое требование идёт сообщением в тот же trip; прошлый план остаётся для сравнения.
      const prev = state?.chosen.component_refs && Object.keys(state.chosen.component_refs).length ? state : previous
      setPrevious(prev)
      setPlanUpdate(null)
      turn.current += 1
      const id = (counter.current += 1)
      const at = turn.current
      setFeed((items) => [...items, { id, turn: at, user: text }])
      await api.sendMessage(trip, text, apply)
      await refresh(trip, prev)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось отправить сообщение')
    } finally {
      setBusy(false)
    }
  }, [apply, previous, refresh, state, tripId])

  const answer = useCallback(async (field: string, value: string, label?: string) => {
    const runId = run.current.runId
    if (!tripId || !runId) return
    setBusy(true)
    setError(null)
    setQuestion(null)
    setResumed(false)
    const id = (counter.current += 1)
    const at = turn.current
    setFeed((items) => [...items, { id, turn: at, user: label ?? (value || 'Пропустить') }])
    try {
      const trace = await api.answer(runId, field, value)
      // Ответ возвращает весь трейс хода: проигрываем только новые события — по одному,
      // чтобы продолжение хода читалось так же, как поток SSE после сообщения.
      const fresh = trace.events.slice(run.current.seen)
      const step = Math.min(160, 2400 / Math.max(fresh.length, 1))
      for (const event of fresh) {
        apply(event)
        await new Promise((resolve) => setTimeout(resolve, step))
      }
      await refresh(tripId, previous)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось отправить ответ')
      await refresh(tripId, previous).catch(() => undefined)
    } finally {
      setBusy(false)
    }
  }, [apply, previous, refresh, tripId])

  const checkout = useCallback(async () => {
    if (!tripId || !state) return
    // Оформление — не ход агента: свой флаг, чтобы чат не показывал «агент думает».
    setOrdering(true)
    setError(null)
    try {
      // Ключ от версии плана: двойной клик не создаёт второй заказ.
      setOrder(await api.checkout(tripId, `${tripId}:v${state.version}`))
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Не удалось оформить')
    } finally {
      setOrdering(false)
    }
  }, [state, tripId])

  const reset = useCallback(() => {
    writePersisted(null)
    run.current = { runId: null, seen: 0 }
    turn.current = 0
    setTripId(null)
    setState(null)
    setPrevious(null)
    setFeed([])
    setQuestion(null)
    setPlanUpdate(null)
    setPhase(null)
    setOrder(null)
    setError(null)
    setResumed(false)
    setSuggestions([])
  }, [])

  return {
    mode: api.mode, tripId, state, previous, feed, question, planUpdate, phase, suggestions,
    busy, ordering, error, order, resumed, send, answer, checkout, reset,
  }
}

export type TripSession = ReturnType<typeof useTripSession>
