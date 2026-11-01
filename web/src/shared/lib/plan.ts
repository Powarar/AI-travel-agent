import type { Candidate, TripState } from '../types/contracts'

import { ruNames } from './placeNames'

export function finalists(state: TripState | null, streamed: Candidate[] = []): Candidate[] {
  const fromState = state?.candidates.destinations.filter((c) => c.ref.finalist === 'true') ?? []
  return fromState.length ? fromState : streamed
}

const POOLS: Record<string, keyof TripState['candidates']> = {
  destination: 'destinations',
  hotel: 'hotels',
  transport_in: 'transport',
  transport_out: 'transport',
  transfer_arrival: 'transfers',
  transfer_departure: 'transfers',
}

export function componentCandidate(state: TripState, component: string): Candidate | null {
  const id = state.chosen.component_refs[component]
  const pool = POOLS[component]
  if (!id || !pool) return null
  return state.candidates[pool].find((c) => c.candidate_id === id) ?? null
}

function time(iso: string | undefined) {
  return iso ? iso.slice(11, 16) : '—'
}

function day(iso: string | undefined) {
  return iso ? `${iso.slice(8, 10)}.${iso.slice(5, 7)}` : ''
}

export function componentLabel(state: TripState, component: string): string {
  const id = state.chosen.component_refs[component]
  const c = componentCandidate(state, component)
  if (!c) return id ?? '—'
  const r = c.ref
  switch (component) {
    case 'destination':
      return [ruNames(r.name), r.country].filter(Boolean).join(', ')
    case 'hotel':
      return `${r.name}${r.stars ? `, ${r.stars}★` : ''}`
    case 'transport_in':
    case 'transport_out':
      return `${day(r.dep_at)} ${time(r.dep_at)} → ${time(r.arr_at)}, ${r.duration_h} ч${r.direct === 'true' ? ', прямой' : ''}`
    default:
      return r.minutes ? `${r.minutes} мин в пути` : c.candidate_id
  }
}

const MONTHS = ['январе', 'феврале', 'марте', 'апреле', 'мае', 'июне', 'июле', 'августе', 'сентябре', 'октябре', 'ноябре', 'декабре']

export function monthName(climateMonth: string | undefined): string {
  const m = Number(climateMonth?.slice(5, 7))
  return m ? MONTHS[m - 1] : ''
}

const MONTHS_GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря']

export function dateRange(start?: string | null, end?: string | null): string | null {
  if (!start || !end) return null
  const [, m1, d1] = start.split('-').map(Number)
  const [, m2, d2] = end.split('-').map(Number)
  return m1 === m2 ? `${d1}–${d2} ${MONTHS_GEN[m1 - 1]}` : `${d1} ${MONTHS_GEN[m1 - 1]} – ${d2} ${MONTHS_GEN[m2 - 1]}`
}

export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few
  return many
}

export interface TripSummary {
  destination: string | null
  country: string | null
  dates: string | null
  nights: number | null
  guests: number | null
  pets: boolean
  dayTemp: string | null
  flightHours: string | null
  origin: string | null
}

/** Шапка поездки — только из TripState: запроса, выбранной корзины и её кандидатов. */
export function summarize(state: TripState): TripSummary {
  const dest = componentCandidate(state, 'destination')
  const flight = componentCandidate(state, 'transport_in')
  const r = state.request
  const adults = r.party?.adults.value ?? null
  const children = r.party?.children.value?.length ?? 0
  return {
    destination: ruNames(dest?.ref.name ?? (state.request as { direction?: { explicit?: { value?: string } } }).direction?.explicit?.value) ?? null,
    country: dest?.ref.country ?? null,
    dates: dateRange(r.dates.start.value, r.dates.end.value),
    nights: r.nights?.value ?? null,
    guests: adults === null ? null : adults + children,
    pets: Boolean(r.party?.pets.value),
    dayTemp: dest?.ref.day_temp_c || null,
    flightHours: flight?.ref.duration_h || dest?.ref.flight_h || null,
    origin: r.origin?.value ?? null,
  }
}
