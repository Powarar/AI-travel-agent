import type { CheckoutResult, RunTrace, SSEEvent, TripState } from '../types/contracts'
import type { TripApi } from './client'
import recorded from './fixtures/golden.json'

// Записанные ответы (T-12: «для разработки использовать fixtures»). Ходы проигрываются по
// порядку; поведение повторяет HTTP: interrupt хранится, ответ идемпотентен по (run_id, field),
// повтор checkout с тем же ключом возвращает тот же заказ. Состояние переживает перезагрузку.

interface Turn {
  kind: 'message' | 'answer'
  field?: string
  events: SSEEvent[]
  state: TripState
}

interface Run extends RunTrace {
  answers: Record<string, string>
}

interface Store {
  cursor: number
  state: TripState | null
  runs: Record<string, Run>
  orders: Record<string, string>
}

const TURNS = (recorded as unknown as { turns: Turn[] }).turns
const KEY = 'okuda.fixture-api'
const STEP_MS = 140

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

function load(): Store {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as Store
  } catch {
    // приватное окно или битый JSON — начинаем с нуля
  }
  return { cursor: 0, state: null, runs: {}, orders: {} }
}

function save(store: Store) {
  try {
    localStorage.setItem(KEY, JSON.stringify(store))
  } catch {
    // без хранилища фикстура живёт до перезагрузки
  }
}

function conflict(message: string): never {
  throw Object.assign(new Error(message), { status: 409 })
}

export class FixtureTripApi implements TripApi {
  readonly mode = 'fixture'

  async createTrip() {
    const tripId = TURNS[0].state.trip_id
    save({ cursor: 0, state: null, runs: {}, orders: {} })
    return tripId
  }

  async sendMessage(tripId: string, _text: string, onEvent: (event: SSEEvent) => void) {
    const store = load()
    if (store.state?.run_status === 'interrupted') conflict('Сначала ответьте на текущий вопрос')
    const turn = TURNS[store.cursor]
    if (!turn || turn.kind !== 'message') conflict('Записанный сценарий закончился — начните поездку заново')
    const runId = crypto.randomUUID()
    const events: SSEEvent[] = [{ event: 'run_started', data: { run_id: runId, trip_id: tripId } }, ...turn.events]
    store.cursor += 1
    store.state = turn.state
    store.runs[runId] = { run_id: runId, trip_id: tripId, status: turn.state.run_status,
      awaiting: turn.state.awaiting_fields, events, answers: {} }
    save(store)
    for (const event of events) {
      await sleep(STEP_MS)
      onEvent(event)
    }
  }

  async answer(runId: string, field: string, value: string): Promise<RunTrace> {
    const store = load()
    const run = store.runs[runId] ?? conflict('Ход не найден')
    if (field in run.answers) {
      if (run.answers[field] !== value) conflict('Ответ на это поле уже сохранён с другим значением')
      return run
    }
    if (run.status !== 'interrupted' || !run.awaiting.includes(field)) conflict('Этот ход не ожидает ответ на указанное поле')
    const turn = TURNS[store.cursor]
    if (!turn || turn.kind !== 'answer') conflict('Записанный сценарий закончился')
    await sleep(STEP_MS * Math.min(turn.events.length, 8))
    store.cursor += 1
    store.state = turn.state
    run.answers[field] = value
    run.events = [...run.events, ...turn.events]
    run.status = turn.state.run_status
    run.awaiting = turn.state.awaiting_fields
    save(store)
    return run
  }

  async getState() {
    return load().state ?? conflict('Состояние ещё не создано')
  }

  async getRun(runId: string) {
    return load().runs[runId] ?? conflict('Ход не найден')
  }

  async checkout(_tripId: string, idempotencyKey: string): Promise<CheckoutResult> {
    const store = load()
    const known = store.orders[idempotencyKey]
    if (known) return { order_id: known, idempotent_replay: true, simulated: true }
    const orderId = store.state?.chosen.component_refs.order ?? `ord-sim-${idempotencyKey.slice(0, 8)}`
    store.orders[idempotencyKey] = orderId
    save(store)
    return { order_id: orderId, idempotent_replay: false, simulated: true }
  }
}
