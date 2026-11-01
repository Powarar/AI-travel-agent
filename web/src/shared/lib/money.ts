import type { Money, PriceLine } from '../types/contracts'

// Деньги приходят Decimal-строкой и складываются в копейках через BigInt: во float не уходят.
const SCALE = 100n

export function toMinor(amount: string): bigint {
  const [whole, frac = ''] = amount.trim().split('.')
  const negative = whole.startsWith('-')
  const minor = BigInt(whole.replace('-', '') || '0') * SCALE + BigInt((frac + '00').slice(0, 2))
  return negative ? -minor : minor
}

export function fromMinor(minor: bigint, currency: string): Money {
  const negative = minor < 0n
  const abs = negative ? -minor : minor
  const frac = abs % SCALE
  const amount = `${negative ? '-' : ''}${abs / SCALE}${frac ? `.${frac.toString().padStart(2, '0')}` : ''}`
  return { amount, currency }
}

const SYMBOL: Record<string, string> = { RUB: '₽', USD: '$', EUR: '€' }

export function formatMoney({ amount, currency }: Money): string {
  const [whole, frac] = amount.split('.')
  const sign = whole.startsWith('-') ? '−' : ''
  const grouped = whole.replace('-', '').replace(/\B(?=(\d{3})+(?!\d))/g, ' ')
  return `${sign}${grouped}${frac ? `,${frac}` : ''} ${SYMBOL[currency] ?? currency}`
}

export interface LedgerGroup {
  kind: string
  total: Money
  lines: PriceLine[]
}

export function groupLedger(items: PriceLine[]): { groups: LedgerGroup[]; total: Money | null } {
  const byKind = new Map<string, PriceLine[]>()
  for (const line of items) byKind.set(line.kind, [...(byKind.get(line.kind) ?? []), line])
  const currency = items[0]?.amount.currency
  if (!currency) return { groups: [], total: null }
  const sum = (lines: PriceLine[]) => lines.reduce((acc, l) => acc + toMinor(l.amount.amount), 0n)
  const groups = [...byKind].map(([kind, lines]) => ({ kind, lines, total: fromMinor(sum(lines), currency) }))
  const included = items.filter((l) => l.included_in_budget)
  return { groups, total: fromMinor(sum(included), currency) }
}
