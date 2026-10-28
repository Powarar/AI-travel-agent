import { HttpTripApi, type TripApi } from './client'
import { FixtureTripApi } from './fixtureApi'

// VITE_API_MODE=http — живой backend через прокси /api; по умолчанию записанные ответы,
// потому что executor backend пока заглушка и сквозной план есть только в записи.
export const api: TripApi = import.meta.env.VITE_API_MODE === 'http' ? new HttpTripApi() : new FixtureTripApi()

export { ApiError } from './client'
export type { TripApi } from './client'
