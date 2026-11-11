import { expect, test } from '@playwright/test'

// Real-time scheduled-task status regression.
//
// The production bug: clicking 立即执行 only produced a 5-second fake spinner,
// and the new session appeared in the left sidebar only after a full reload.
// These specs drive the real WS frame path (useWebSocket queue -> useChatSocket
// CustomEvent -> ScheduledTasksPage / ChatPage handlers) with stubbed HTTP APIs,
// so no backend, database or credentials are required.

const TASK_ID = 'task-e2e-1'
const RUN_ID = 'run-e2e-1'
const SESSION_ID = 'sess-e2e-1'
const TASK_NAME = '每日AI简报'

const task = {
  id: TASK_ID,
  name: TASK_NAME,
  instruction: '整理过去 24 小时的 AI 新闻',
  schedule: { type: 'day', hour: '10', minute: '00' },
  enabled: true,
  last_status: 'success',
  workspace_id: 'default',
  created_at: 1_700_000_000,
}

const successRun = {
  id: RUN_ID,
  task_id: TASK_ID,
  fired_at: 1_700_000_100,
  started_at: 1_700_000_101,
  finished_at: 1_700_000_130,
  status: 'success',
  session_id: SESSION_ID,
  error: null,
  duration_s: 29.5,
}

const runningRun = {
  ...successRun,
  status: 'running',
  finished_at: null,
  session_id: null,
  error: null,
  duration_s: null,
}

const frame = (type: string, data: Record<string, unknown>) =>
  JSON.stringify({ type, conversation_id: null, seq: 0, ts: Date.now(), data })

type MockSocket = { send: (message: string) => void }

test.describe('自动化任务实时状态', () => {
  // 桩 API / mock socket 是每个用例自己的状态；默认配置 fullyParallel=true 时
  // 强制本组串行，避免跨 worker 之外的同文件并发读到彼此的 runs。
  test.describe.configure({ mode: 'serial' })

  let runs: unknown[]
  let socket: MockSocket | null = null

  test.beforeEach(async ({ page }) => {
    runs = []
    socket = null

    await page.addInitScript(() => {
      localStorage.setItem(
        'prismclaw.auth.v1',
        JSON.stringify({ token: 'e2e-token', userId: 'e2e-user', nickname: '测试用户' }),
      )
      localStorage.setItem('prismclaw-section', 'files')
    })

    await page.routeWebSocket('**/api/ws', (ws) => {
      socket = ws as MockSocket
      // The page assigns onmessage right after `new WebSocket()`. Sending the
      // handshake from inside the route callback can race that assignment, so
      // defer it. The specs do not depend on wsState being 'ready'.
      setTimeout(() => ws.send(frame('hello', {})), 50)
      ws.onMessage((message) => {
        if (String(message).includes('"identify"')) {
          ws.send(frame('ready', { conversations: [], protocol_version: 1 }))
        }
      })
    })

    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const json = (body: unknown) =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(body),
        })
      if (path === '/api/ws') return route.continue()
      if (path === '/api/workspaces') {
        return json({ workspaces: [{ id: 'default', name: '默认工作区' }] })
      }
      if (path === '/api/sessions') return json({ items: [], has_more: false })
      if (path === '/api/scheduled-tasks' && route.request().method() === 'GET') {
        return json([task])
      }
      if (path === `/api/scheduled-tasks/${TASK_ID}/runs`) return json(runs)
      if (path === `/api/scheduled-tasks/${TASK_ID}/trigger`) {
        return json({ run_id: RUN_ID, status: 'pending' })
      }
      return json({})
    })
  })

  test('立即执行后由 WS 事件驱动卡片状态，并在完成时实时插入左侧会话', async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('任务列表', { exact: true })).toBeVisible()
    await expect.poll(() => socket !== null).toBe(true)

    // 手动触发：乐观排队态来自 trigger 响应，而不是 5 秒假定时器。
    await page.locator('[data-scheduled-menu-button]').first().click()
    await page.locator('.overlay-panel button').filter({ hasText: '立即执行' }).click()
    await expect(page.getByText('排队中…').first()).toBeVisible()
    // 旧实现会在 5 秒后无条件清掉状态；真状态必须一直保留到真实终态事件。
    await page.waitForTimeout(5_500)
    await expect(page.getByText('排队中…').first()).toBeVisible()

    // 后端在发 started 之前已把 run 写成 running，/runs 同步反映。
    runs = [runningRun]
    socket!.send(frame('scheduled_task.started', { task_id: TASK_ID, run_id: RUN_ID }))
    await expect(page.getByText('执行中…').first()).toBeVisible()
    await expect(page.locator('.sidebar-nav-row[title="' + TASK_NAME + '"]')).toHaveCount(0)

    // 后端终态写库先于 completed 广播；完成后卡片显示终态、左侧无需刷新即出现会话。
    runs = [successRun]
    socket!.send(
      frame('scheduled_task.completed', {
        task_id: TASK_ID,
        run_id: RUN_ID,
        status: 'success',
        session_id: SESSION_ID,
        duration_s: 29.5,
        error: null,
      }),
    )
    await expect(page.getByText(/上次成功/)).toBeVisible()
    await expect(page.getByText('执行中…')).toHaveCount(0)
    await expect(page.locator('.sidebar-nav-row[title="' + TASK_NAME + '"]')).toHaveCount(1)
    await expect(page.getByText('查看会话')).toBeVisible()
  })

  test('重进页面由 /runs 恢复终态，不依赖任何 WS 事件', async ({ page }) => {
    runs = [successRun]
    await page.goto('/')
    await expect(page.getByText('任务列表', { exact: true })).toBeVisible()
    await expect(page.getByText(/上次成功/)).toBeVisible()
    await expect(page.getByText('查看会话')).toBeVisible()
  })

  test('崩溃残留的 running 行显示未完成，不误报为上次失败', async ({ page }) => {
    // at-most-once 语义下进程崩溃会让 run 永久停在 running。
    runs = [runningRun]
    await page.goto('/')
    await expect(page.getByText('任务列表', { exact: true })).toBeVisible()
    await expect(page.getByText('上次执行未完成')).toBeVisible()
    await expect(page.getByText('上次失败')).toHaveCount(0)
  })
})
