import type { Outcome } from '../types/contracts'

export const OUTCOME_LABEL: Record<Outcome, string> = {
  plan_ready: 'План готов',
  awaiting_answer: 'Ждём ответа',
  best_effort: 'Лучший найденный вариант',
  infeasible: 'Так не складывается',
  degraded: 'План на устаревших данных',
  unsupported: 'Этого мы не делаем',
}

export const PHASE_LABEL: Record<string, string> = {
  PARSE: 'Разбор запроса',
  CLARIFY: 'Уточнение',
  INSPIRE: 'Подбор направлений',
  DECIDE: 'Выбор направления',
  SEARCH: 'Поиск',
  COMPOSE: 'Сборка плана',
  VALIDATE: 'Проверка стыковок и бюджета',
  REPAIR: 'Исправление',
  SHORTLIST: 'Финалисты',
  COMPARE: 'Сравнение',
  EXPLAIN: 'Объяснение',
  PLAN: 'План',
  CHECKOUT: 'Оформление',
}

export const TOOL_LABEL: Record<string, string> = {
  suggest_destinations: 'направления',
  weather_climate: 'климат',
  transport_search: 'перелёты',
  hotel_search: 'отели',
  transfer_search: 'трансферы',
  checkout_mock: 'оформление',
}

export const KIND_LABEL: Record<string, string> = {
  transport: 'Перелёт',
  lodging: 'Проживание',
  transfer: 'Трансфер',
  activities: 'Активности',
}

export const COMPONENT_LABEL: Record<string, string> = {
  destination: 'Направление',
  hotel: 'Отель',
  transport_in: 'Перелёт туда',
  transport_out: 'Перелёт обратно',
  transfer_arrival: 'Трансфер из аэропорта',
  transfer_departure: 'Трансфер в аэропорт',
}

export const FIELD_LABEL: Record<string, string> = {
  origin: 'Город вылета',
  'dates.start': 'Даты',
  'budget.amount': 'Бюджет',
  'party.adults': 'Сколько взрослых',
}
