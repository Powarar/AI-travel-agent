import { telegramInitData } from '../lib/telegram'
import type { CheckoutResult, OrderOut, RunTrace, SSEEvent, TripState } from '../types/contracts'
import { readSSE } from './sse'

// Единая граница экрана с бэкендом. Источник истины — TripState и события; текст — подпись.
export interface TripApi {
  readonly mode: 'http' | 'mock'
  /** Правки, которые стоит предложить чипами после плана. */
  suggestions(): string[]
  createTrip(): Promise<string>
  sendMessage(tripId: string, text: string, onEvent: (event: SSEEvent) => void): Promise<void>
  answer(runId: string, field: string, value: string): Promise<RunTrace>
  getState(tripId: string): Promise<TripState>
  getRun(runId: string): Promise<RunTrace>
  checkout(tripId: string, idempotencyKey: string): Promise<CheckoutResult>
  /** Заказы текущего пользователя: Telegram — по initData, браузер — по своему client id. */
  listOrders(): Promise<OrderOut[]>
}

export class ApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message)
  }
}

async function json<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new ApiError(response.status, typeof body.detail === 'string' ? body.detail : `Ошибка ${response.status}`)
  }
  return response.json() as Promise<T>
}

const CLIENT_KEY = 'okuda.client-id'

function clientId(): string {
  try {
    const known = localStorage.getItem(CLIENT_KEY)
    if (known) return known
    const fresh = crypto.randomUUID()
    localStorage.setItem(CLIENT_KEY, fresh)
    return fresh
  } catch {
    return crypto.randomUUID()
  }
}

/** Кто оформляет: в Mini App — подписанный initData, на сайте — анонимный id этого браузера. */
export function identityHeaders(): Record<string, string> {
  const initData = telegramInitData()
  return initData ? { Authorization: `tma ${initData}` } : { 'X-Client-Id': clientId() }
}

export class HttpTripApi implements TripApi {
  readonly mode = 'http'

  constructor(private readonly base = '/api/v1') {}

  suggestions() {
    return ['Давай лучше в Сочи, перелёт покороче', 'У нас собака', 'Нас будет трое']
  }

  async createTrip() {
    const body = await json<{ trip_id: string }>(await fetch(`${this.base}/trips`, { method: 'POST' }))
    return body.trip_id
  }

  async sendMessage(tripId: string, text: string, onEvent: (event: SSEEvent) => void) {
    const response = await fetch(`${this.base}/trips/${tripId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ text }),
    })
    if (!response.ok) await json(response)
    for await (const event of readSSE(response)) onEvent(event)
  }

  async answer(runId: string, field: string, value: string) {
    return json<RunTrace>(await fetch(`${this.base}/runs/${runId}/answers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ field, value }),
    }))
  }

  async getState(tripId: string) {
    return json<TripState>(await fetch(`${this.base}/trips/${tripId}/state`))
  }

  async getRun(runId: string) {
    return json<RunTrace>(await fetch(`${this.base}/runs/${runId}`))
  }

  async checkout(tripId: string, idempotencyKey: string) {
    return json<CheckoutResult>(await fetch(`${this.base}/trips/${tripId}/checkout`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey, ...identityHeaders() },
    }))
  }

  async listOrders() {
    return json<OrderOut[]>(await fetch(`${this.base}/orders`, { headers: identityHeaders() }))
  }
}
