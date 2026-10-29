import { HttpTripApi, type TripApi } from './client'
import { MockTripApi } from './mock/mockApi'

// В обычном запуске клиент разговаривает с backend через прокси /api. Иначе любой новый
// запрос молча попадал в один из заранее записанных мок-сценариев (например, «Киев» — в
// сценарий Паттайи), хотя пользователь задал жёсткое направление. Для офлайн-демо мок
// включается явно: VITE_API_MODE=mock.
export const api: TripApi = import.meta.env.VITE_API_MODE === 'mock' ? new MockTripApi() : new HttpTripApi()

export { ApiError } from './client'
export type { TripApi } from './client'
