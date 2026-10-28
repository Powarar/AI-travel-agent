import { HttpTripApi, type TripApi } from './client'
import { FixtureTripApi } from './fixtureApi'

// По умолчанию — живой backend через прокси /api (ход исполняет агент).
// VITE_API_MODE=fixture — записанный прогон каталога: демо без backend и без сети.
export const api: TripApi = import.meta.env.VITE_API_MODE === 'fixture' ? new FixtureTripApi() : new HttpTripApi()

export { ApiError } from './client'
export type { TripApi } from './client'
