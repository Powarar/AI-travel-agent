import { useId, useState, type FormEvent } from 'react'

import { ApiError } from '../../shared/api'
import { BrandLockup } from '../../shared/ui/BrandLockup/BrandLockup'
import { Button } from '../../shared/ui/Button/Button'
import { Icon } from '../../shared/ui/Icon/Icon'
import { TypingDots } from '../../shared/ui/TypingDots/TypingDots'
import { useAuth } from './useAuth'

import styles from './AuthScreen.module.scss'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const PASSWORD_MIN = 8
const PASSWORD_MAX = 128

type Mode = 'login' | 'register'

export function AuthScreen() {
  const { login, register } = useAuth()
  const [mode, setMode] = useState<Mode>('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [errors, setErrors] = useState<{ email?: string; password?: string }>({})
  const [serverError, setServerError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const emailId = useId()
  const passwordId = useId()

  function switchMode(next: Mode) {
    setMode(next)
    setServerError(null)
    setErrors({})
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setServerError(null)
    const next: { email?: string; password?: string } = {}
    if (!EMAIL_RE.test(email.trim())) next.email = 'Введите корректный email'
    if (password.length < PASSWORD_MIN || password.length > PASSWORD_MAX) {
      next.password = `Пароль должен быть от ${PASSWORD_MIN} до ${PASSWORD_MAX} символов`
    }
    setErrors(next)
    if (next.email || next.password) return
    setSubmitting(true)
    try {
      await (mode === 'login' ? login : register)(email.trim(), password)
    } catch (e) {
      // detail с backend уже на русском (401/409/422/429) — показываем как есть; сетевой сбой — своей строкой.
      setServerError(e instanceof ApiError ? e.message : 'Не удалось связаться с сервером. Проверьте подключение и попробуйте ещё раз.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className={styles.page}>
      <main className={styles.card} aria-labelledby="auth-title">
        <BrandLockup />
        <div className={styles.switch} role="group" aria-label="Вход или регистрация">
          <button type="button" className={mode === 'login' ? styles.switchOn : styles.switchOff}
            aria-pressed={mode === 'login'} onClick={() => switchMode('login')}>Войти</button>
          <button type="button" className={mode === 'register' ? styles.switchOn : styles.switchOff}
            aria-pressed={mode === 'register'} onClick={() => switchMode('register')}>Регистрация</button>
        </div>
        <h1 id="auth-title" className={styles.title}>{mode === 'login' ? 'С возвращением' : 'Создайте аккаунт'}</h1>
        <p className={styles.lead}>
          {mode === 'login'
            ? 'Войдите, чтобы продолжить подбор поездок и видеть свои заказы.'
            : 'Аккаунт хранит историю заказов — она доступна с любого устройства.'}
        </p>
        <form className={styles.form} onSubmit={submit} noValidate>
          <div className={styles.field}>
            <label className={styles.label} htmlFor={emailId}>Email</label>
            <input
              id={emailId}
              className={styles.input}
              type="email"
              name="email"
              autoComplete="email"
              inputMode="email"
              placeholder="you@example.com"
              value={email}
              disabled={submitting}
              aria-invalid={Boolean(errors.email)}
              aria-describedby={errors.email ? `${emailId}-error` : undefined}
              onChange={(e) => { setEmail(e.target.value); setErrors((p) => ({ ...p, email: undefined })) }}
            />
            {errors.email && <span className={styles.fieldError} id={`${emailId}-error`}>{errors.email}</span>}
          </div>
          <div className={styles.field}>
            <label className={styles.label} htmlFor={passwordId}>Пароль</label>
            <input
              id={passwordId}
              className={styles.input}
              type="password"
              name="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              placeholder="••••••••"
              value={password}
              disabled={submitting}
              aria-invalid={Boolean(errors.password)}
              aria-describedby={`${passwordId}-hint${errors.password ? ` ${passwordId}-error` : ''}`}
              onChange={(e) => { setPassword(e.target.value); setErrors((p) => ({ ...p, password: undefined })) }}
            />
            <span className={styles.hint} id={`${passwordId}-hint`}>От {PASSWORD_MIN} до {PASSWORD_MAX} символов</span>
            {errors.password && <span className={styles.fieldError} id={`${passwordId}-error`}>{errors.password}</span>}
          </div>
          {serverError && (
            <p className={styles.error} role="alert"><Icon name="alert" size={16} /> {serverError}</p>
          )}
          <Button type="submit" className={styles.submit} disabled={submitting}>
            {submitting ? 'Подождите…' : mode === 'login' ? 'Войти' : 'Зарегистрироваться'}
          </Button>
        </form>
      </main>
    </div>
  )
}

/** Экран на время молчаливого refresh при загрузке: без него мелькает форма входа. */
export function AuthLoading() {
  return (
    <div className={styles.page}>
      <div className={styles.loading} role="status" aria-label="Проверяем вход">
        <BrandLockup />
        <TypingDots label="Проверяем вход" />
      </div>
    </div>
  )
}