import { COMPONENT_LABEL } from '../../shared/lib/labels'
import { formatMoney, fromMinor, groupLedger, toMinor } from '../../shared/lib/money'
import { componentLabel } from '../../shared/lib/plan'
import type { SSEPayloads, TripState } from '../../shared/types/contracts'

import styles from './PlanDiff.module.scss'

interface Props {
  previous: TripState
  current: TripState
  update: SSEPayloads['plan_updated']
}

export function PlanDiff({ previous, current, update }: Props) {
  const before = groupLedger(previous.chosen.items).total
  const after = groupLedger(current.chosen.items).total
  const delta = before && after && before.currency === after.currency
    ? fromMinor(toMinor(after.amount) - toMinor(before.amount), after.currency)
    : null

  return (
    <section className={styles.diff} aria-labelledby="diff-title">
      <h3 id="diff-title">Сравнение с планом v{previous.version}</h3>
      <table>
        <thead>
          <tr><th scope="col">Компонент</th><th scope="col">Было</th><th scope="col">Стало</th></tr>
        </thead>
        <tbody>
          {update.changed_components.map((c) => (
            <tr key={c} className={styles.changed}>
              <th scope="row">{COMPONENT_LABEL[c] ?? c}</th>
              <td>{componentLabel(previous, c)}</td>
              <td>{componentLabel(current, c)}</td>
            </tr>
          ))}
          {update.kept_components.map((c) => (
            <tr key={c} className={styles.kept}>
              <th scope="row">{COMPONENT_LABEL[c] ?? c}</th>
              <td colSpan={2}>{componentLabel(current, c)} — сохранено</td>
            </tr>
          ))}
        </tbody>
        {before && after && (
          <tfoot>
            <tr>
              <th scope="row">Итого</th>
              <td>{formatMoney(before)}</td>
              <td>{formatMoney(after)}{delta && <small> ({delta.amount.startsWith('-') ? '' : '+'}{formatMoney(delta)})</small>}</td>
            </tr>
          </tfoot>
        )}
      </table>
      <p className={styles.note}>
        Изменено {update.changed_components.length}, сохранено {update.kept_components.length} — по событию plan_updated.
      </p>
    </section>
  )
}
