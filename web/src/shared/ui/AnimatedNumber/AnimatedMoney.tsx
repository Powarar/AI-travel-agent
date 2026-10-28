import { formatMoney, toMinor } from '../../lib/money'
import { useCountUp } from '../../lib/useCountUp'
import type { Money } from '../../types/contracts'

/**
 * Сумма, которая «досчитывается» до значения. Анимируются целые рубли только на экране;
 * последний кадр рисует исходную Decimal-строку, так что итог совпадает с состоянием до копейки.
 */
export function AnimatedMoney({ value, className }: { value: Money; className?: string }) {
  const rubles = Number(toMinor(value.amount) / 100n)
  const shown = useCountUp(rubles)
  const text = shown === rubles ? formatMoney(value) : formatMoney({ amount: String(shown), currency: value.currency })
  return <span className={className} aria-label={formatMoney(value)}>{text}</span>
}
