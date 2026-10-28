// Зеркало shared/contracts и SSE-контракта (services/agent/docs/CONTRACTS.md).
// Описаны только поля, которые читает экран; остальное проходит как есть.

export type DataStatus = 'verified' | 'partially_verified' | 'stale' | 'unknown' | 'synthetic' | 'real'
export type RunStatus = 'running' | 'interrupted' | 'completed' | 'failed'
export type Outcome = 'plan_ready' | 'awaiting_answer' | 'best_effort' | 'infeasible' | 'degraded' | 'unsupported'

export interface Money {
  amount: string // Decimal строкой — во float не переводится
  currency: string
}

export interface PriceLine {
  line_id: string
  kind: 'transport' | 'lodging' | 'activities' | 'transfer' | string
  amount: Money
  scope: string
  included_in_budget: boolean
  source_id: string
  fetched_at: string
  data_status: DataStatus
  description: string | null
}

export interface Candidate {
  candidate_id: string
  kind: string
  ref: Record<string, string>
  score: number | null
  data_status: DataStatus
}

export interface Violation {
  code: string
  severity: 'blocking' | 'warning' | string
  affected_component: string
  expected: string | null
  actual: string | null
}

export interface FieldValue<T> {
  value: T | null
  status: 'known' | 'inferred' | 'defaulted' | 'unknown'
}

export interface TripState {
  trip_id: string
  version: number
  stage: string
  run_status: RunStatus
  awaiting_fields: string[]
  request: {
    budget: { amount: FieldValue<Money> }
    dates: { start: FieldValue<string>; end: FieldValue<string> }
    [key: string]: unknown
  }
  candidates: {
    destinations: Candidate[]
    transport: Candidate[]
    hotels: Candidate[]
    transfers: Candidate[]
  }
  chosen: { items: PriceLine[]; component_refs: Record<string, string> }
  violations: Violation[]
  provenance: Record<string, DataStatus>
  outcome: Outcome | null
  budgets: { tool_calls: number; llm_calls: number; questions_asked: number; wall_clock_ms: number }
}

export interface SSEPayloads {
  run_started: { run_id: string; trip_id: string }
  phase_changed: { from: string; to: string }
  llm_call: { slot: string; ms: number; tokens_in: number; tokens_out: number }
  tool_started: { tool: string; args_hash: string; from_cache: boolean }
  tool_completed: { tool: string; ms: number; count: number; status: string }
  tool_failed: { tool: string; attempt: number; policy: string; will_retry: boolean }
  question: { field: string; text: string; options: string[]; allow_skip: boolean }
  candidates: { kind: string; items: Candidate[]; ranking_factors: string[] }
  violation: { code: string; severity: string; expected: string | null; actual: string | null; affected_component: string }
  repair_started: { violation_code: string; action_signature: string; iteration: number }
  plan_updated: { version: number; changed_components: string[]; kept_components: string[] }
  answer_text: { block_id: string; text: string; numbers_verified: boolean }
  checkout_completed: { order_id: string; idempotent_replay: boolean; simulated: true }
  budget_exhausted: { limit_hit: string; best_effort_version: number; unresolved: string[] }
}

export type SSEEventName = keyof SSEPayloads
export type SSEEvent = { [K in SSEEventName]: { event: K; data: SSEPayloads[K] } }[SSEEventName]

export interface RunTrace {
  run_id: string
  trip_id: string
  status: RunStatus
  awaiting: string[]
  events: SSEEvent[]
}

export interface CheckoutResult {
  order_id: string
  idempotent_replay: boolean
  simulated: true
}
