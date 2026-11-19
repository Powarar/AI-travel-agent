import type { CheckoutResult, OrderOut, RunTrace, SSEEvent, TripState } from '../types/contracts'
import { authorizedFetch } from './auth'
import { json } from './http'
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
  /** Заказы текущего пользователя: Telegram — по initData, браузер — по Bearer-токену входа. */
  listOrders(): Promise<OrderOut[]>
}

export class HttpTripApi implements TripApi {
  readonly mode = 'http'

  constructor(private readonly base = '/api/v1') {}

  suggestions() {
    return ['Давай лучше в Сочи, перелёт покороче', 'У нас собака', 'Нас будет трое']
  }

  async createTrip() {
    const body = await json<{ trip_id: string }>(await authorizedFetch(`${this.base}/trips`, { method: 'POST' }))
    return body.trip_id
  }

  async sendMessage(tripId: string, text: string, onEvent: (event: SSEEvent) => void) {
    const response = await authorizedFetch(`${this.base}/trips/${tripId}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify({ text }),
    })
    if (!response.ok) await json(response)
    for await (const event of readSSE(response)) onEvent(event)
  }

  async answer(runId: string, field: string, value: string) {
    return json<RunTrace>(await authorizedFetch(`${this.base}/runs/${runId}/answers`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ field, value }),
    }))
  }

  async getState(tripId: string) {
    return json<TripState>(await authorizedFetch(`${this.base}/trips/${tripId}/state`))
  }

  async getRun(runId: string) {
    return json<RunTrace>(await authorizedFetch(`${this.base}/runs/${runId}`))
  }

  async checkout(tripId: string, idempotencyKey: string) {
    return json<CheckoutResult>(await authorizedFetch(`${this.base}/trips/${tripId}/checkout`, {
      method: 'POST',
      headers: { 'Idempotency-Key': idempotencyKey },
    }))
  }

  async listOrders() {
    return json<OrderOut[]>(await authorizedFetch(`${this.base}/orders`))
  }
}