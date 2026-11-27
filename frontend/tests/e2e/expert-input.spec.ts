import { expect, test } from '@playwright/test'

const SESSION_ID = 'sess-expert-e2e'
const SKILL_ID = 'writing/draft'
const emptyExpertState = {
  expert_id: null,
  source: null,
  suppressed: false,
  selected_skill_ids: [],
}

const frame = (type: string, data: Record<string, unknown> = {}) =>
  JSON.stringify({ type, conversation_id: SESSION_ID, seq: 0, ts: Date.now(), data })

test.describe('专家与技能输入区', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript((sessionId) => {
      localStorage.setItem(
        'prismclaw.auth.v1',
        JSON.stringify({ token: 'e2e-token', userId: 'e2e-user', nickname: '测试用户' }),
      )
      localStorage.setItem('prismclaw-active-conv', sessionId)
    }, SESSION_ID)

    await page.routeWebSocket('**/api/ws', (ws) => {
      setTimeout(() => ws.send(frame('hello')), 50)
      ws.onMessage((message) => {
        if (String(message).includes('"identify"')) {
          ws.send(frame('ready', { conversations: [], protocol_version: 1 }))
        }
      })
    })
  })

  test('加号菜单包含上传/引用/专家/技能/连接器，未选专家时无药丸', async ({ page }) => {
    await page.route('**/api/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const json = (body: unknown) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
      if (path === '/api/workspaces') {
        return json({ workspaces: [{ id: 'default', name: '默认工作区' }] })
      }
      if (path === '/api/sessions') {
        return json({
          items: [{ id: SESSION_ID, title: '专家测试', created_at: '2026-01-01T00:00:00Z' }],
          has_more: false,
        })
      }
      if (path === `/api/sessions/${SESSION_ID}`) {
        return json({ id: SESSION_ID, message_count: 0, expert_state: emptyExpertState })
      }
      if (path === `/api/sessions/${SESSION_ID}/messages`) return json([])
      if (path === '/api/experts' || path === '/api/skills' || path === '/api/skills/list') {
        return json([])
      }
      return json({})
    })

    await page.goto('/')
    await page.getByRole('button', { name: '添加', exact: true }).click()

    const menu = page.getByRole('menu')
    await expect(menu.getByRole('menuitem', { name: '上传文件', exact: true })).toBeVisible()
    await expect(menu.getByRole('menuitem', { name: '引用文件', exact: true })).toBeVisible()
    await expect(menu.getByRole('menuitem', { name: '专家', exact: true })).toBeVisible()
    await expect(menu.getByRole('menuitem', { name: '技能', exact: true })).toBeVisible()
    await expect(menu.getByRole('menuitem', { name: '连接器', exact: true })).toBeVisible()
    // 只有专家与技能是子菜单项，右侧带展开箭头
    await expect(menu.locator('svg.lucide-chevron-right')).toHaveCount(2)

    await expect(page.locator('[data-expert-pill]')).toHaveCount(0)
    const send = page.getByRole('button', { name: '发送', exact: true })
    await expect(send).toBeDisabled()
    await page.getByRole('textbox', { name: /发消息/ }).fill('直接提问')
    await expect(send).toBeEnabled()
  })

  test('技能 chip 可增删，随消息回放并在刷新后恢复', async ({ page }) => {
    let selectedSkillIds: string[] = []
    const messages: Array<Record<string, unknown>> = []

    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const json = (body: unknown) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
      if (path === '/api/workspaces') {
        return json({ workspaces: [{ id: 'default', name: '默认工作区' }] })
      }
      if (path === '/api/sessions') {
        return json({
          items: [{ id: SESSION_ID, title: '技能测试', created_at: '2026-01-01T00:00:00Z' }],
          has_more: false,
        })
      }
      if (path === `/api/sessions/${SESSION_ID}`) {
        return json({
          id: SESSION_ID,
          message_count: messages.length,
          expert_state: { ...emptyExpertState, selected_skill_ids: selectedSkillIds },
        })
      }
      if (path === `/api/sessions/${SESSION_ID}/messages`) return json(messages)
      if (path === '/api/experts') return json([])
      if (path === '/api/skills') {
        return json([{
          expert_name: 'writing',
          expert_description: '写作技能包',
          skills: [{ id: SKILL_ID, name: 'draft', name_zh: '草稿', description: '生成草稿' }],
        }])
      }
      if (path === '/api/skills/list') return json([])
      if (path === `/api/sessions/${SESSION_ID}/skills` && request.method() === 'PUT') {
        selectedSkillIds = request.postDataJSON().selected_skill_ids
        return json({ session_id: SESSION_ID, selected_skill_ids: selectedSkillIds })
      }
      const deletePrefix = `/api/sessions/${SESSION_ID}/skills/`
      if (path.startsWith(deletePrefix) && request.method() === 'DELETE') {
        const skillId = decodeURIComponent(path.slice(deletePrefix.length))
        selectedSkillIds = selectedSkillIds.filter((id) => id !== skillId)
        return json({ session_id: SESSION_ID, selected_skill_ids: selectedSkillIds })
      }
      return json({})
    })

    await page.goto('/')
    const editor = page.getByRole('textbox', { name: /发消息/ })
    await editor.fill('写一段介绍')

    await page.getByRole('button', { name: '添加', exact: true }).click()
    await page.getByRole('menuitem', { name: '技能', exact: true }).click()
    const putRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/api/sessions/${SESSION_ID}/skills`) && request.method() === 'PUT',
    )
    await page.getByRole('button', { name: /草稿/ }).click()
    await putRequest
    await expect(editor.locator('[data-chip][data-kind="skill"]')).toHaveCount(1)
    await expect(editor.locator('.input-chip-label')).toHaveText('draft')

    const deleteRequest = page.waitForRequest((request) =>
      request.url().endsWith(`/api/sessions/${SESSION_ID}/skills/${SKILL_ID}`) && request.method() === 'DELETE',
    )
    await page.getByRole('button', { name: '移除技能 draft', exact: true }).click()
    await deleteRequest
    await expect(editor.locator('[data-chip][data-kind="skill"]')).toHaveCount(0)

    await page.getByRole('button', { name: /草稿/ }).click()
    await expect(editor.locator('[data-chip][data-kind="skill"]')).toHaveCount(1)
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.locator('.msg-chip .msg-chip-label')).toHaveText('draft')

    await page.reload()
    await expect(page.getByRole('textbox', { name: /发消息/ }).locator('[data-chip][data-kind="skill"]')).toHaveCount(1)
  })

  test('快速连续选择多个技能不会丢失前一次选择', async ({ page }) => {
    let selectedSkillIds: string[] = []
    const putBodies: string[][] = []
    let firstPut = true

    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const json = (body: unknown) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
      if (path === '/api/workspaces') {
        return json({ workspaces: [{ id: 'default', name: '默认工作区' }] })
      }
      if (path === '/api/sessions') {
        return json({
          items: [{ id: SESSION_ID, title: '技能并发测试', created_at: '2026-01-01T00:00:00Z' }],
          has_more: false,
        })
      }
      if (path === `/api/sessions/${SESSION_ID}`) {
        return json({
          id: SESSION_ID,
          message_count: 0,
          expert_state: { ...emptyExpertState, selected_skill_ids: selectedSkillIds },
        })
      }
      if (path === `/api/sessions/${SESSION_ID}/messages`) return json([])
      if (path === '/api/experts' || path === '/api/skills/list') return json([])
      if (path === '/api/skills') {
        return json([{
          expert_name: 'writing',
          expert_description: '写作技能包',
          skills: [
            { id: SKILL_ID, name: 'draft', name_zh: '草稿', description: '生成草稿' },
            { id: 'writing/review', name: 'review', name_zh: '复核', description: '复核文稿' },
          ],
        }])
      }
      if (path === `/api/sessions/${SESSION_ID}/skills` && request.method() === 'PUT') {
        const nextIds = request.postDataJSON().selected_skill_ids as string[]
        putBodies.push(nextIds)
        if (firstPut) {
          firstPut = false
          await new Promise((resolve) => setTimeout(resolve, 200))
        }
        selectedSkillIds = nextIds
        return json({ session_id: SESSION_ID, selected_skill_ids: selectedSkillIds })
      }
      return json({})
    })

    await page.goto('/')
    await page.getByRole('button', { name: '添加', exact: true }).click()
    await page.getByRole('menuitem', { name: '技能', exact: true }).click()
    await page.getByRole('button', { name: /草稿/ }).click()
    await page.getByRole('button', { name: /复核/ }).click()

    await expect.poll(() => putBodies.length).toBe(2)
    await expect.poll(() => [...selectedSkillIds].sort()).toEqual([SKILL_ID, 'writing/review'].sort())
    const editor = page.getByRole('textbox', { name: /发消息/ })
    await expect(editor.locator('[data-chip][data-kind="skill"]')).toHaveCount(2)
  })

  test('快速连续切换专家以最后一次选择为准', async ({ page }) => {
    let expertState = { ...emptyExpertState }
    const postExpertIds: string[] = []
    let firstPost = true
    let releaseFirstPost = () => {}
    const firstPostBlocked = new Promise<void>((resolve) => { releaseFirstPost = resolve })

    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const json = (body: unknown) => route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
      if (path === '/api/workspaces') {
        return json({ workspaces: [{ id: 'default', name: '默认工作区' }] })
      }
      if (path === '/api/sessions') {
        return json({
          items: [{ id: SESSION_ID, title: '专家并发测试', created_at: '2026-01-01T00:00:00Z' }],
          has_more: false,
        })
      }
      if (path === `/api/sessions/${SESSION_ID}`) {
        return json({ id: SESSION_ID, message_count: 0, expert_state: expertState })
      }
      if (path === `/api/sessions/${SESSION_ID}/messages`) return json([])
      if (path === '/api/skills' || path === '/api/skills/list') return json([])
      if (path === '/api/experts') {
        return json([
          {
            id: 'writer', expertType: 'agent',
            displayName: { en: 'Writing Expert', zh: '写作专家' },
            description: '生成文稿', avatar: '',
          },
          {
            id: 'research-team', expertType: 'team',
            displayName: { en: 'Research Team', zh: '研究团队' },
            description: '协作研究', avatar: '',
          },
        ])
      }
      if (path === `/api/sessions/${SESSION_ID}/expert` && request.method() === 'POST') {
        const expertId = request.postDataJSON().expert_id as string
        postExpertIds.push(expertId)
        if (firstPost) {
          firstPost = false
          await firstPostBlocked
        }
        expertState = { ...emptyExpertState, expert_id: expertId, source: 'user' }
        return json(expertState)
      }
      return json({})
    })

    await page.goto('/')
    await page.getByRole('button', { name: '添加', exact: true }).click()
    await page.getByRole('menuitem', { name: '专家', exact: true }).click()
    await page.getByRole('button', { name: /写作专家/ }).click()
    await expect.poll(() => postExpertIds.length).toBe(1)
    await page.getByRole('menuitem', { name: '专家', exact: true }).click()
    await page.getByRole('button', { name: /研究团队/ }).click()

    releaseFirstPost()
    await expect.poll(() => postExpertIds.length).toBe(2)
    await expect.poll(() => expertState.expert_id).toBe('research-team')
    await expect(page.locator('[data-expert-pill]')).toContainText('研究团队')
  })

  test('专家单选显示药丸，失败保留权威状态，取消后消失', async ({ page }) => {
    let expertState = { ...emptyExpertState }
    let failNextDelete = false

    await page.route('**/api/**', async (route) => {
      const request = route.request()
      const path = new URL(request.url()).pathname
      const json = (body: unknown, status = 200) => route.fulfill({
        status,
        contentType: 'application/json',
        body: JSON.stringify(body),
      })
      if (path === '/api/workspaces') {
        return json({ workspaces: [{ id: 'default', name: '默认工作区' }] })
      }
      if (path === '/api/sessions') {
        return json({
          items: [{ id: SESSION_ID, title: '专家药丸测试', created_at: '2026-01-01T00:00:00Z' }],
          has_more: false,
        })
      }
      if (path === `/api/sessions/${SESSION_ID}`) {
        return json({ id: SESSION_ID, message_count: 0, expert_state: expertState })
      }
      if (path === `/api/sessions/${SESSION_ID}/messages`) return json([])
      if (path === '/api/skills' || path === '/api/skills/list') return json([])
      if (path === '/api/experts') {
        return json([
          {
            id: 'writer', expertType: 'agent',
            displayName: { en: 'Writing Expert', zh: '写作专家' },
            profession: { en: 'Writer', zh: '写作' }, description: '生成文稿',
            avatar: '/avatars/writer.svg', members: [],
          },
          {
            id: 'research-team', expertType: 'team',
            displayName: { en: 'Research Team', zh: '研究团队' },
            profession: { en: 'Research', zh: '研究' }, description: '协作研究',
            avatar: '/avatars/research.svg', members: [],
          },
        ])
      }
      if (path === `/api/sessions/${SESSION_ID}/expert` && request.method() === 'POST') {
        expertState = {
          ...emptyExpertState,
          expert_id: request.postDataJSON().expert_id,
          source: 'user',
        }
        return json(expertState)
      }
      if (path === `/api/sessions/${SESSION_ID}/expert` && request.method() === 'DELETE') {
        if (failNextDelete) {
          failNextDelete = false
          return json({ detail: 'cancel failed' }, 500)
        }
        expertState = { ...emptyExpertState, suppressed: true }
        return json(expertState)
      }
      return json({})
    })

    await page.goto('/')
    await page.getByRole('button', { name: '添加', exact: true }).click()
    await page.getByRole('menuitem', { name: '专家', exact: true }).click()
    const panel = page.getByPlaceholder('搜索专家')
    await expect(panel).toBeVisible()
    await expect(page.getByRole('button', { name: /写作专家/ })).toBeVisible()
    await expect(page.getByRole('button', { name: /研究团队/ })).toBeVisible()

    await page.getByRole('button', { name: /写作专家/ }).click()
    const pill = page.locator('[data-expert-pill]')
    await expect(pill).toContainText('写作专家')

    await pill.getByRole('button', { name: '写作专家', exact: true }).click()
    await page.getByRole('button', { name: /研究团队/ }).click()
    await expect(pill).toContainText('研究团队')

    failNextDelete = true
    const failedDelete = page.waitForResponse((response) =>
      response.url().endsWith(`/api/sessions/${SESSION_ID}/expert`)
      && response.request().method() === 'DELETE'
      && response.status() === 500,
    )
    await page.getByRole('button', { name: '取消专家 研究团队', exact: true }).click()
    await failedDelete
    await expect(pill).toContainText('研究团队')

    await page.getByRole('button', { name: '取消专家 研究团队', exact: true }).click()
    await expect(pill).toHaveCount(0)
  })
})
