import { isMiniApp, telegramInitData } from '../lib/telegram'
import { json } from './http'

// Сессия входа. Access-токен живёт только в памяти вкладки: перезагрузка страницы его
// стирает, и он не утекает через localStorage при XSS. Refresh-токен backend отдаёт в теле
// JSON (не httpOnly-cookie), поэтому его приходится класть в localStorage — это осознанный
// компромисс: он доступен скриптам при XSS, но позволяет молча продлевать вход между
// перезагрузками. Access-токен в памяти ограничивает окно кражи одним сеансом.
const REFRESH_KEY = 'okuda.refresh'
const BASE = '/api/v1'

export interface AuthUser {
  id: string
  email: string
}

interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
  user: AuthUser
}

export interface AuthState {
  status: 'loading' | 'authenticated' | 'anonymous'
  user: AuthUser | null
}

let accessToken: string | null = null
let refreshToken: string | null = readRefresh()
let snapshot: AuthState = { status: 'loading', user: null }
const listeners = new Set<() => void>()

function readRefresh(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY)
  } catch {
    return null
  }
}

function writeRefresh(token: string | null) {
  try {
    if (token) localStorage.setItem(REFRESH_KEY, token)
    else localStorage.removeItem(REFRESH_KEY)
  } catch {
    // без хранилища вход живёт до перезагрузки
  }
}

function emit(next: AuthState) {
  snapshot = next
  listeners.forEach((notify) => notify())
}

export function subscribeAuth(notify: () => void) {
  listeners.add(notify)
  return () => listeners.delete(notify)
}

export function getAuthState(): AuthState {
  return snapshot
}

function applySession(data: TokenResponse) {
  accessToken = data.access_token
  refreshToken = data.refresh_token
  writeRefresh(data.refresh_token)
  emit({ status: 'authenticated', user: data.user })
}

function clearSession() {
  accessToken = null
  refreshToken = null
  writeRefresh(null)
  emit({ status: 'anonymous', user: null })
}

/** Заголовок авторизации: в Mini App — подписанный initData, на сайте — Bearer из памяти. */
export function authHeaders(): Record<string, string> {
  const initData = telegramInitData()
  if (initData) return { Authorization: `tma ${initData}` }
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {}
}

let refreshPromise: Promise<boolean> | null = null

async function doRefresh(): Promise<boolean> {
  const token = refreshToken
  if (!token) {
    clearSession()
    return false
  }
  try {
    const data = await json<TokenResponse>(await fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    }))
    applySession(data)
    return true
  } catch {
    clearSession()
    return false
  }
}

// Single-flight: параллельные 401 делят один запрос refresh, а не запускают N штук.
function refreshSession(): Promise<boolean> {
  refreshPromise ??= doRefresh().finally(() => {
    refreshPromise = null
  })
  return refreshPromise
}

/**
 * fetch с авторизацией и одним повтором после молчаливого refresh. В Mini App refresh не
 * нужен: там подпись initData, и 401 отдаётся вызывающему как есть.
 */
export async function authorizedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const send = () => {
    const headers = new Headers(init.headers)
    for (const [key, value] of Object.entries(authHeaders())) headers.set(key, value)
    return fetch(url, { ...init, headers })
  }
  const response = await send()
  if (response.status === 401 && !isMiniApp && refreshToken) {
    const renewed = await refreshSession()
    if (renewed) return send()
  }
  return response
}

export async function login(email: string, password: string): Promise<void> {
  applySession(await json<TokenResponse>(await fetch(`${BASE}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })))
}

export async function register(email: string, password: string): Promise<void> {
  applySession(await json<TokenResponse>(await fetch(`${BASE}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  })))
}

export async function logout(): Promise<void> {
  const token = refreshToken
  // Сначала гасим локальный вход, чтобы экран отреагировал сразу; отзыв на сервере — best-effort.
  clearSession()
  if (!token) return
  try {
    await fetch(`${BASE}/auth/logout`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: token }),
    })
  } catch {
    // сеть недоступна — локальная сессия уже очищена
  }
}

// Старт: в Mini App вход через Telegram, на сайте — молчаливый refresh, если он сохранён.
if (isMiniApp) {
  emit({ status: 'authenticated', user: null })
} else if (refreshToken) {
  emit({ status: 'loading', user: null })
  void refreshSession()
} else {
  emit({ status: 'anonymous', user: null })
}