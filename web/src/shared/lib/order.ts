import type { OrderSnapshot, TripState } from '../types/contracts'
import { fromMinor, groupLedger, toMinor } from './money'
import { componentCandidate } from './plan'

/** Зеркало services/backend/app/api/orders.py: снимок плана на момент оформления — для мока. */
export function buildSnapshot(state: TripState): OrderSnapshot {
  const dest = componentCandidate(state, 'destination')
  const r = state.request
  const adults = r.party?.adults.value ?? null
  const groups = new Map<string, { count: number; minor: bigint; currency: string }>()
  for (const line of state.chosen.items) {
    const g = groups.get(line.kind) ?? { count: 0, minor: 0n, currency: line.amount.currency }
    groups.set(line.kind, { ...g, count: g.count + 1, minor: g.minor + toMinor(line.amount.amount) })
  }
  return {
    destination: dest?.ref.name ?? (r as { direction?: { explicit?: { value?: string } } }).direction?.explicit?.value ?? null,
    country: dest?.ref.country ?? null,
    date_start: r.dates.start.value,
    date_end: r.dates.end.value,
    nights: r.nights?.value ?? null,
    guests: adults === null ? null : adults + (r.party?.children.value?.length ?? 0),
    pets: Boolean(r.party?.pets.value),
    origin: r.origin?.value ?? null,
    total: groupLedger(state.chosen.items).total,
    lines: [...groups].map(([kind, g]) => ({ kind, count: g.count, total: fromMinor(g.minor, g.currency) })),
  }
}
