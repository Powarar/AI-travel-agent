import type { Candidate, TripState } from '../types/contracts'

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
      return [r.name, r.country].filter(Boolean).join(', ')
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
