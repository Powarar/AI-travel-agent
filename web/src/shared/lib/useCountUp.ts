import { useEffect, useRef, useState } from 'react'

const reduced = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Плавный пересчёт целого числа к цели. Последний кадр — ровно цель, без округлений по пути. */
export function useCountUp(target: number, duration = 900): number {
  const [value, setValue] = useState(target)
  const from = useRef(target)

  useEffect(() => {
    const start = from.current
    if (start === target || reduced()) {
      from.current = target
      setValue(target)
      return
    }
    let frame = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / duration)
      const eased = 1 - Math.pow(1 - p, 3)
      setValue(p === 1 ? target : Math.round(start + (target - start) * eased))
      if (p < 1) frame = requestAnimationFrame(tick)
      else from.current = target
    }
    frame = requestAnimationFrame(tick)
    return () => {
      cancelAnimationFrame(frame)
      from.current = target
    }
  }, [target, duration])

  return value
}
