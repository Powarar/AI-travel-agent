import { buildSnapshot } from '../../lib/order'
import type { CheckoutResult, OrderOut, RunTrace, SSEEvent, TripState } from '../../types/contracts'
import type { TripApi } from '../client'
import recorded from './scenarios.json'

// Мок API: ответы записаны прогоном настоящего агента (AgentRunExecutor + мок-слоты на
// каталоге data/seeds), поэтому события и TripState совпадают с живым backend по форме и
// числам. Сценарий выбирается по первому сообщению, правка — по ключевым словам.
// Поведение повторяет HTTP: interrupt хранится, ответ идемпотентен по (run_id, field),
// повтор checkout с тем же ключом отдаёт тот же заказ, всё переживает перезагрузку.

interface Turn {
  kind: 'message' | 'answer'
  field?: string
  value?: string
  text?: string
  events: SSEEvent[]
  state: TripState
}

interface Scenario {
  id: string
  message: string
  turns: Turn[]
  deltas: Record<string, Turn>
}

interface Run extends RunTrace {
  answers: Record<string, string>
}

interface Store {
  scenario: string | null
  cursor: number
  state: TripState | null
  runs: Record<string, Run>
  orders: Record<string, string>
}

const SCENARIOS = (recorded as unknown as { scenarios: Scenario[] }).scenarios
const KEY = 'okuda.mock-api'
// Заказы — отдельно от сценария: новая поездка сбрасывает сценарий, но не историю заказов.
const ORDERS_KEY = 'okuda.mock-orders'

function loadOrders(): OrderOut[] {
  try {
    return JSON.parse(localStorage.getItem(ORDERS_KEY) ?? '[]') as OrderOut[]
  } catch {
    return []
  }
}

const PICK: [RegExp, string][] = [
  [/гамбург|берлин|париж|нью-?йорк/i, 'hamburg'],
  [/сочи|адлер/i, 'sochi'],
]

const DELTAS: [RegExp, string][] = [
  [/сочи|адлер|покороче|ближе/i, 'sochi'],
  [/собак|пёс|пес\b|питом|кот/i, 'dog'],
  [/тро(е|ём)|втроём|3 (человек|гост)/i, 'three'],
  [/бюджет|дешевле|подешевле|\d{2,}/i, 'budget'],
]

/** Подсказки, какие правки есть в записи, — их показывает экран вместо «угадайте». */
export const MOCK_SUGGESTIONS: Record<string, string[]> = {
  'warm-sea': ['Давай лучше в Сочи, перелёт покороче', 'У нас собака', 'Нас будет трое', 'Бюджет 150 000'],
  sochi: ['У нас собака', 'Нас будет трое', 'Бюджет 70 000'],
  hamburg: ['Хочу в Сочи'],
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

// Темп воспроизведения: фазы и инструменты — быстро, вопрос и план — с паузой «подумать».
function pause(event: SSEEvent): number {
  switch (event.event) {
    case 'phase_changed':
      return 260
    case 'tool_started':
      return 90
    case 'tool_completed':
      return 180
    case 'question':
    case 'answer_text':
      return 650
    case 'plan_updated':
    case 'candidates':
      return 380
    default:
      return 160
  }
}

function load(): Store {
  try {
    const raw = localStorage.getItem(KEY)
    if (raw) return JSON.parse(raw) as Store
  } catch {
    // приватное окно или битый JSON — начинаем с нуля
  }
  return { scenario: null, cursor: 0, state: null, runs: {}, orders: {} }
}

function save(store: Store) {
  try {
    localStorage.setItem(KEY, JSON.stringify(store))
  } catch {
    // без хранилища мок живёт до перезагрузки
  }
}

function conflict(message: string): never {
  throw Object.assign(new Error(message), { status: 409 })
}

function scenarioFor(text: string): Scenario {
  const id = PICK.find(([re]) => re.test(text))?.[1] ?? 'warm-sea'
  return SCENARIOS.find((s) => s.id === id) ?? SCENARIOS[0]
}

function withTrip(state: TripState, tripId: string, version: number): TripState {
  return { ...state, trip_id: tripId, version }
}

function retag(events: SSEEvent[], version: number): SSEEvent[] {
  return events.map((e) => (e.event === 'plan_updated' ? { ...e, data: { ...e.data, version } } : e))
}

export class MockTripApi implements TripApi {
  readonly mode = 'mock'

  suggestions(): string[] {
    const id = load().scenario
    return id ? MOCK_SUGGESTIONS[id] ?? [] : []
  }

  async createTrip() {
    save({ scenario: null, cursor: 0, state: null, runs: {}, orders: {} })
    return crypto.randomUUID()
  }

  async sendMessage(tripId: string, text: string, onEvent: (event: SSEEvent) => void) {
    const store = load()
    if (store.state?.run_status === 'interrupted') conflict('Сначала ответьте на текущий вопрос')
    const version = (store.state?.version ?? 0) + 1
    let turn: Turn | undefined
    if (!store.scenario) {
      const scenario = scenarioFor(text)
      store.scenario = scenario.id
      store.cursor = 1
      turn = scenario.turns[0]
    } else {
      const scenario = SCENARIOS.find((s) => s.id === store.scenario)!
      const key = DELTAS.find(([re]) => re.test(text))?.[1]
      turn = key ? scenario.deltas[key] : undefined
    }
    const runId = crypto.randomUUID()
    const started: SSEEvent = { event: 'run_started', data: { run_id: runId, trip_id: tripId } }
    let events: SSEEvent[]
    if (turn) {
      store.state = withTrip(turn.state, tripId, version)
      events = [started, ...retag(turn.events, version)]
    } else {
      // В записи такой правки нет: честно говорим об этом, план не трогаем.
      const hint = this.suggestions().join(' · ')
      store.state = { ...(store.state as TripState), version }
      events = [started, { event: 'answer_text', data: {
        block_id: 'mock', numbers_verified: true,
        text: `Так поменять поездку пока не получится — план остался прежним. Можно попробовать: ${hint}.`,
      } }]
    }
    store.runs[runId] = { run_id: runId, trip_id: tripId, status: store.state.run_status,
      awaiting: store.state.awaiting_fields, events, answers: {} }
    save(store)
    for (const event of events) {
      await sleep(pause(event))
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
    const scenario = SCENARIOS.find((s) => s.id === store.scenario)
    const turn = scenario?.turns[store.cursor]
    if (!turn || turn.kind !== 'answer') conflict('Записанный сценарий закончился')
    await sleep(700)
    const version = (store.state?.version ?? 0) + 1
    store.cursor += 1
    store.state = withTrip(turn.state, run.trip_id, version)
    run.answers[field] = value
    run.events = [...run.events, ...retag(turn.events, version)]
    run.status = store.state.run_status
    run.awaiting = store.state.awaiting_fields
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
    await sleep(900)
    const store = load()
    const known = store.orders[idempotencyKey]
    if (known) return { order_id: known, idempotent_replay: true, simulated: true }
    const orderId = store.state?.chosen.component_refs.order ?? `ord-sim-${idempotencyKey.slice(0, 8)}`
    store.orders[idempotencyKey] = orderId
    save(store)
    if (store.state) {
      const order: OrderOut = { order_id: orderId, trip_id: store.state.trip_id, created_at: new Date().toISOString(),
        simulated: true, snapshot: buildSnapshot(store.state) }
      try {
        localStorage.setItem(ORDERS_KEY, JSON.stringify([order, ...loadOrders()]))
      } catch {
        // без хранилища заказ оформлен, но в истории не останется
      }
    }
    return { order_id: orderId, idempotent_replay: false, simulated: true }
  }

  async listOrders() {
    return loadOrders()
  }
}
