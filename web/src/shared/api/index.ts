import { HttpTripApi, type TripApi } from './client'
import { MockTripApi } from './mock/mockApi'

// По умолчанию — мок: записанные ходы настоящего агента, без backend, сети и трат на LLM.
// VITE_API_MODE=http — живой backend через прокси /api (ход исполняет агент).
export const api: TripApi = import.meta.env.VITE_API_MODE === 'http' ? new HttpTripApi() : new MockTripApi()

export { ApiError } from './client'
export type { TripApi } from './client'
