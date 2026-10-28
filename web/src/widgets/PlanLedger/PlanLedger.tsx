import { COMPONENT_LABEL, KIND_LABEL, OUTCOME_LABEL } from '../../shared/lib/labels'
import { formatMoney, fromMinor, groupLedger, toMinor } from '../../shared/lib/money'
import { componentCandidate, componentLabel } from '../../shared/lib/plan'
import type { DataStatus, SSEPayloads, TripState } from '../../shared/types/contracts'
import { StatusBadge } from '../../shared/ui/StatusBadge/StatusBadge'

import styles from './PlanLedger.module.scss'

const ORDER = ['destination', 'transport_in', 'hotel', 'transfer_arrival', 'transfer_departure', 'transport_out']

function statuses(lines: { data_status: DataStatus }[]): DataStatus[] {
  return [...new Set(lines.map((l) => l.data_status))]
}

interface Props {
  state: TripState
  explanation: SSEPayloads['answer_text'] | null
}

export function PlanLedger({ state, explanation }: Props) {
  const { groups, total } = groupLedger(state.chosen.items)
  const budget = state.request.budget.amount.value
  const remaining = budget && total && budget.currency === total.currency
    ? fromMinor(toMinor(budget.amount) - toMinor(total.amount), total.currency)
    : null
  const over = remaining ? remaining.amount.startsWith('-') : false
  const blocking = state.violations.filter((v) => v.severity === 'blocking')
  const components = ORDER.filter((c) => c in state.chosen.component_refs)

  return (
    <section className={styles.panel} aria-labelledby="plan-title">
      <header className={styles.head}>
        <div>
          <span className={`${styles.outcome} ${styles[state.outcome ?? 'unknown'] ?? ''}`.trim()}>
            {state.outcome ? OUTCOME_LABEL[state.outcome] : state.stage}
          </span>
          <h3 id="plan-title">План v{state.version}</h3>
        </div>
        <div className={styles.provenance}>
          {Object.entries(state.provenance).map(([key, status]) => (
            <StatusBadge key={key} status={status} prefix={key} />
          ))}
        </div>
      </header>

      {blocking.length > 0 && (
        <ul className={styles.violations}>
          {state.violations.map((v, i) => (
            <li key={`${v.code}-${i}`} className={v.severity === 'blocking' ? styles.blocking : undefined}>
              <b>{v.code}</b> · {COMPONENT_LABEL[v.affected_component] ?? v.affected_component}: ждали {v.expected ?? '—'}, вышло {v.actual ?? '—'}
            </li>
          ))}
        </ul>
      )}

      {components.length > 0 && (
        <dl className={styles.components}>
          {components.map((c) => {
            const candidate = componentCandidate(state, c)
            return (
              <div key={c}>
                <dt>{COMPONENT_LABEL[c] ?? c}</dt>
                <dd>
                  {componentLabel(state, c)}
                  {candidate && <StatusBadge status={candidate.data_status} />}
                </dd>
              </div>
            )
          })}
        </dl>
      )}

      {total && (
        <table className={styles.ledger}>
          <caption>Разбивка цены — строки BudgetLedger из состояния</caption>
          <tbody>
            {groups.map((g) => (
              <tr key={g.kind}>
                <th scope="row">{KIND_LABEL[g.kind] ?? g.kind}<small>{g.lines.length} строк</small></th>
                <td>{statuses(g.lines).map((s) => <StatusBadge key={s} status={s} />)}</td>
                <td className={styles.num}>{formatMoney(g.total)}</td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr><th scope="row">Итого</th><td /><td className={styles.num}>{formatMoney(total)}</td></tr>
            {budget && <tr><th scope="row">Бюджет</th><td /><td className={styles.num}>{formatMoney(budget)}</td></tr>}
            {remaining && (
              <tr className={over ? styles.over : styles.left}>
                <th scope="row">{over ? 'Сверх бюджета' : 'Остаток'}</th><td />
                <td className={styles.num}>{formatMoney(remaining)}</td>
              </tr>
            )}
          </tfoot>
        </table>
      )}

      {explanation && (
        <figure className={styles.explain}>
          <blockquote>{explanation.text}</blockquote>
          <figcaption>
            Текст — подпись к плану; цифры выше взяты из состояния.
            {explanation.numbers_verified ? ' Числа в тексте сверены с состоянием.' : ' Числа в тексте не сверены!'}
          </figcaption>
        </figure>
      )}

      <p className={styles.meta}>
        Инструментов {state.budgets.tool_calls} · вызовов модели {state.budgets.llm_calls} · уточнений {state.budgets.questions_asked}
      </p>
    </section>
  )
}
