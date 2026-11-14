// Реальные скриншоты демо: headless Chromium проходит живой стек через nginx
// (регистрация → вопросы → план → оформление → перезагрузка → правка плана → выход)
// и кладёт PNG в docs/img/. Запуск: `make demo-shots` при поднятом `make up`.
// Chromium берётся из CHROME_PATH либо из кэша Playwright (`npx playwright install chromium`).
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright-core'

const OUT = fileURLToPath(new URL('../../docs/img/', import.meta.url))
const ARTIFACTS = fileURLToPath(new URL('../../artifacts/', import.meta.url))
const BASE = process.env.DEMO_BASE_URL ?? 'http://localhost:8080'
const EMAIL = `demo+${Date.now()}@okuda.dev`
const PASSWORD = 'Demo-Pass-2026!'

fs.mkdirSync(OUT, { recursive: true })
fs.mkdirSync(ARTIFACTS, { recursive: true })

const report = { steps: [], consoleErrors: [], failedRequests: [], refreshCalls: 0, injected401: 0 }
const t0 = Date.now()
function step(name, extra = {}) {
  const entry = { name, atMs: Date.now() - t0, ...extra }
  report.steps.push(entry)
  console.log(`STEP ${name} +${entry.atMs}ms`, JSON.stringify(extra))
}

// Кэш Playwright мог остаться от другой версии библиотеки: берём любой найденный
// Chromium той же раскладки, вместо того чтобы требовать скачать ровно свой ревизион.
function resolveExecutable() {
  const expected = chromium.executablePath()
  if (fs.existsSync(expected)) return expected
  const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH,
    path.join(os.homedir(), 'Library/Caches/ms-playwright'),
    path.join(os.homedir(), '.cache/ms-playwright')].filter(Boolean)
  for (const root of roots) {
    if (!fs.existsSync(root)) continue
    const revisions = fs.readdirSync(root).filter((d) => /^chromium-\d+$/.test(d)).sort().reverse()
    for (const revision of revisions) {
      const tail = expected.slice(expected.search(/chromium-\d+/)).replace(/^chromium-\d+/, revision)
      const candidate = path.join(root, tail)
      if (fs.existsSync(candidate)) return candidate
    }
  }
  return expected
}

const executablePath = process.env.CHROME_PATH ?? resolveExecutable()
const browser = await chromium.launch({ executablePath, headless: true })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 2 })
page.on('console', (m) => {
  if (m.type() === 'error') report.consoleErrors.push(m.text())
})
page.on('pageerror', (e) => report.consoleErrors.push(`pageerror: ${e.message}`))
page.on('requestfailed', (r) => report.failedRequests.push(`${r.url()} ${r.failure()?.errorText}`))
page.on('response', (r) => {
  if (r.url().includes('/auth/refresh')) report.refreshCalls += 1
})

const shot = (name, fullPage = false) => page.screenshot({ path: `${OUT}/${name}.png`, fullPage })

async function answerQuestions(max = 4) {
  for (let i = 0; i < max; i += 1) {
    const card = page.locator('[aria-label="Вопрос агента"]')
    try {
      await card.waitFor({ state: 'visible', timeout: 4000 })
    } catch {
      return i
    }
    const label = await card.locator('button').first().textContent()
    step('question', { option: label?.trim() })
    if (i === 0) await shot('03-question')
    await card.locator('button').first().click()
    await page.waitForTimeout(1200)
  }
  return max
}

try {
  // Прогрев: после смены зависимостей Vite дооптимизирует их на первом заходе и
  // обрывает in-flight модули full-reload'ом. Этот заход в доказательство не идёт.
  await page.goto(BASE, { waitUntil: 'networkidle' })
  report.consoleErrors.length = 0
  report.failedRequests.length = 0

  await page.goto(BASE, { waitUntil: 'domcontentloaded' })
  await page.getByRole('button', { name: 'Регистрация' }).waitFor({ timeout: 30000 })
  await shot('01-auth')
  step('auth-screen')

  await page.getByRole('button', { name: 'Регистрация' }).click()
  await page.getByRole('textbox', { name: 'Email' }).fill(EMAIL)
  await page.getByRole('textbox', { name: 'Пароль' }).fill(PASSWORD)
  await page.locator('form button[type=submit]').click()
  await page.locator('#planner').waitFor({ timeout: 30000 })
  step('registered', { email: EMAIL })
  await page.waitForTimeout(600)
  await shot('02-home', true)

  await page.locator('#examples button').first().click()
  await page.getByRole('button', { name: /Подобрать поездку/ }).click()
  step('trip-request-sent')
  await answerQuestions()
  await page.getByText('Всё готово — можно оформлять').waitFor({ timeout: 90000 })
  await page.waitForTimeout(900)
  await shot('04-plan', true)
  step('plan-ready')

  // Один искусственный 401 на /orders: клиент обязан молча обновить токен и повторить.
  await page.route('**/api/v1/orders', async (route) => {
    report.injected401 += 1
    await route.fulfill({ status: 401, contentType: 'application/json', body: '{"detail":"expired"}' })
  }, { times: 1 })
  await page.getByRole('button', { name: /Оформить/ }).click()
  await page.getByText('Заказ создан').waitFor({ timeout: 30000 })
  await page.waitForTimeout(1200)
  await shot('05-order')
  step('checkout', { injected401: report.injected401, refreshCalls: report.refreshCalls })

  await page.reload({ waitUntil: 'domcontentloaded' })
  await page.getByText('Всё готово — можно оформлять').waitFor({ timeout: 30000 })
  await page.waitForTimeout(900)
  await shot('06-resume', true)
  step('session-resumed-after-reload', { refreshCalls: report.refreshCalls })

  await page.getByRole('textbox', { name: 'Изменить требования' }).fill('Давай лучше в Сочи, перелёт покороче')
  await page.getByRole('button', { name: 'Отправить' }).click()
  step('second-message-sent')
  await answerQuestions()
  await page.locator('#diff-title').waitFor({ timeout: 90000 })
  await page.getByText('Всё готово — можно оформлять').waitFor({ timeout: 30000 })
  await page.waitForTimeout(900)
  await shot('07-plan-diff', true)
  step('plan-diff')

  await page.getByRole('button', { name: 'Выйти из аккаунта' }).click()
  await page.getByRole('button', { name: 'Регистрация' }).waitFor({ timeout: 15000 })
  await shot('08-logged-out')
  step('logged-out')
} catch (error) {
  report.failure = String((error && error.stack) || error)
  await page.screenshot({ path: `${ARTIFACTS}/demo-shots-failure.png`, fullPage: true }).catch(() => {})
  step('FAILED')
} finally {
  // 401 ниже — искусственный: сценарий сам подставляет его один раз на /orders.
  report.consoleErrors = report.consoleErrors.filter((e) => !e.includes('telegram-web-app.js'))
  fs.writeFileSync(`${ARTIFACTS}/demo-shots-report.json`, JSON.stringify(report, null, 2))
  console.log('REPORT', JSON.stringify({
    steps: report.steps.length,
    failure: report.failure ?? null,
    consoleErrors: report.consoleErrors,
    failedRequests: report.failedRequests,
    refreshCalls: report.refreshCalls,
    injected401: report.injected401,
  }, null, 2))
  await browser.close()
}
process.exit(report.failure ? 1 : 0)