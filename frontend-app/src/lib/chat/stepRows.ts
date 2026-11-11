import type { OperationDetail, TimelineNode } from '../../types'
import { extractArgObject, isAskUserToolName, parseAskUserInteraction } from './timelineParse'

/* ─── L1/L2 共享的步骤行数据层（2026-09-02 按设计图重构）─────────────────
 * 设计：纵向 timeline，思考与操作同轴混排——
 *   - 思考（iteration.reasoning）是独立时间轴行：⏱ 时钟图标 + 单行灰字
 *   - 操作行单行文本：「读取 StepList.tsx」「编辑 stepRows.ts」
 *   - 读取/编辑图标区分：读取 = 文档+放大镜（file_read），编辑 = 文档+铅笔（file_edit）
 * 每个操作（OperationDetail）一行；每轮 iteration 先出思考行再出操作行。
 * ────────────────────────────────────────────────────────────────── */

export type IconType =
  | 'file_read'   // 📄🔍 读取文件
  | 'file_edit'   // 📄✏️ 编辑/写入文件
  | 'web_search'  // 🌐 联网检索（设计图标准：地球图标 + 「网络搜索」标签）
  | 'search'      // 🔍 本地检索（grep/glob/ls/memory_search 等）
  | 'fetch'       // 🌐 历史会话的 web_fetch / web_extract（工具已下线）
  | 'command'     // 🖥️ 执行 shell 命令（控制台）
  | 'execute'     // ⚙️ skill / 交互类执行（激活技能、交付文件、询问用户等）
  | 'ask_user'    // ❓ 向用户提问（HITL AskUserQuestion）
  | 'clock'       // ⏱️ 思考行
  | 'check'       // ✓ 执行完成
  | 'default'     // 📌 其他

export interface StepRow {
  key: string
  /** 行类型：reasoning（思考行）/ operation（操作行） */
  kind: 'reasoning' | 'operation'
  /** 两段式标题的深色前缀（如「网络搜索」「深度思考」）；无则单段渲染 */
  label?: string
  /** 单行显示文本（CSS 截断）；思考行不携带（标题只留 label） */
  text?: string
  /** 工具类型对应的图标 */
  iconType: IconType
  /** 操作状态（running | done | error）；思考行恒为 done */
  nodeStatus: string
  ops: OperationDetail[]
  /** 思考行完整原文（L2 展开用）；操作行不携带 */
  reasoningFull?: string
  /** 操作目标（完整路径/查询/URL，title 与 L2 展开用） */
  target?: string
  /** HITL 用户答案（仅 ask_user 行）：回答到达后展示在行标题下方 */
  answer?: string
}

/** 时间线汇总（折叠头「思考执行，读取了N个文件」） */
export interface TimelineSummary {
  readCount: number
  editCount: number
  otherOpCount: number
  reasoningCount: number
  /** 折叠头摘要文本，如「思考执行，读取了5个文件，编辑了3个文件」 */
  label: string
}

/** 目标 → 文件名（路径最后一段；非路径原样） */
function basenameOf(target: string): string {
  const t = target.trim()
  if (!t) return ''
  const seg = t.split(/[\\/]/).filter(Boolean).pop() ?? t
  return seg
}

/** 工具名 → 图标类型 + 动词（语义化命名，避免原始工具名太随意） */
function classifyOp(op: OperationDetail): { iconType: IconType; verb: string } {
  const n = (op.toolName ?? '').toLowerCase()

  // ── 精确匹配优先 ──
  if (n === 'ls' || n === 'list_dir' || n === 'list_directory') return { iconType: 'search', verb: '浏览目录' }
  if (n === 'glob' || n === 'find') return { iconType: 'search', verb: '查找文件' }
  if (n === 'grep' || n === 'rg') return { iconType: 'search', verb: '搜索内容' }
  if (n === 'skill_search') return { iconType: 'search', verb: '搜索技能' }
  if (n === 'tool_search') return { iconType: 'search', verb: '搜索工具' }
  if (n === 'activate_skill') return { iconType: 'execute', verb: '激活技能' }
  if (n === 'present_files') return { iconType: 'execute', verb: '交付文件' }
  if (isAskUserToolName(n)) return { iconType: 'ask_user', verb: '请求用户澄清' }
  if (n === 'stop_command') return { iconType: 'execute', verb: '终止命令' }
  if (n === 'todo_write') return { iconType: 'execute', verb: '更新任务列表' }
  if (n === 'web_fetch' || n === 'web_extract') return { iconType: 'fetch', verb: '抓取网页' }
  // 联网检索（设计图标准）：与 StepDetailList.isWebSearchTool 同一判据，
  // 行标题 = 「网络搜索」+ 查询词
  if (n === 'web_search' || n.startsWith('web') || n.includes('__search') || n.includes(':search')) {
    return { iconType: 'web_search', verb: '网络搜索' }
  }
  if (n.startsWith('delete') || n === 'rm' || n.startsWith('remove')) return { iconType: 'file_edit', verb: '删除文件' }

  // ── 前缀兜底 ──
  if (n.startsWith('write') || n.startsWith('create')) return { iconType: 'file_edit', verb: '创建文件' }
  if (n.startsWith('edit') || n.startsWith('patch')) return { iconType: 'file_edit', verb: '编辑文件' }
  if (n.startsWith('read') || n === 'cat') return { iconType: 'file_read', verb: '读取文件' }
  if (n.includes('search')) return { iconType: 'search', verb: '搜索' }
  if (n.startsWith('bash') || n.startsWith('execute') || n.startsWith('terminal') || n.startsWith('shell')) return { iconType: 'command', verb: '执行命令' }
  if (n.includes('skill')) return { iconType: 'execute', verb: '调用技能' }
  if (n.includes('memory')) return { iconType: 'search', verb: '检索记忆' }
  return { iconType: 'default', verb: '执行操作' }
}

/** 标题行只显示动词、不带具体指令的操作——命令内容/目录路径/任务列表等
 *  参数冗长且不适合做副标题预览，仅保留在展开卡内查看（设计图标准） */
const TITLE_VERB_ONLY = new Set(['执行命令', '浏览目录', '更新任务列表'])

/** 操作行目标摘要（两段式标题的灰色后半段）：文件操作取文件名，其余取
 *  参数对象首行；无目标返回空串，此时该行只渲染深色动词前缀 */
function opRowDetail(verb: string, iconType: IconType, target: string): string {
  if (TITLE_VERB_ONLY.has(verb)) return ''
  if (!target) return ''
  if (iconType === 'file_read' || iconType === 'file_edit') return basenameOf(target) || ''
  return target
}

/**
 * 构建时间轴行：每轮 iteration 先出一条思考行（⏱），再逐个操作各一行。
 *
 * 后端 step 节点粒度粗——一个 step 可包含多轮 iteration，每轮有
 * reasoning + 扁平 OperationDetail[]（按 startedAt 升序）。
 * 顶层 reasoning 节点也作为思考行按时序混排。
 *
 * 调用方必须保证入参只含本模块认识的节点类型（useSessionLoader
 * 的水合过滤兜掉未知类型，见该文件注释）——此处不做未知类型兜底，
 * 否则真实数据缺陷会被静默吞掉。
 */
export function buildStepRows(nodes: TimelineNode[]): StepRow[] {
  const rows: StepRow[] = []

  const pushReasoning = (text: string, key: string) => {
    const trimmed = text.trim()
    if (!trimmed) return
    rows.push({
      key,
      kind: 'reasoning',
      // 标题行只显示「深度思考」标签；思考原文仅在展开卡展示（reasoningFull）
      label: '深度思考',
      iconType: 'clock',
      nodeStatus: 'done',
      ops: [],
      reasoningFull: trimmed,
    })
  }

  for (const n of nodes) {
    if (n.type === 'reasoning') {
      pushReasoning(n.content, `reasoning-${rows.length}`)
      continue
    }
    for (const it of n.iterations) {
      // 把该轮的 reasoning + text（自然语言输出）合并进同一张展开卡：
      // reasoning 在上，text 在下，用分隔线隔开。只有 text（无 reasoning）
      // 时也 push 一行但 label 改成「迭代过语」——每轮都必有一条思考/过语行。
      const reasonTxt = (it.reasoning ?? '').trim()
      const iterTxt = (it.text ?? '').trim()
      if (reasonTxt || iterTxt) {
        const merged = [
          reasonTxt && `【思考】\n${reasonTxt}`,
          iterTxt && `【迭代过语】\n${iterTxt}`,
        ].filter(Boolean).join('\n\n')
        rows.push({
          key: `${n.type}-it${it.segmentId}-reasoning`,
          kind: 'reasoning',
          label: reasonTxt ? '深度思考' : '迭代过语',
          iconType: 'clock',
          nodeStatus: 'done',
          ops: [],
          reasoningFull: merged,
        })
      }

      // 扁平 OperationDetail[] 单层循环（扁平化重构后不再有 OperationGroup 折叠层）
      for (const op of it.operations) {
        const { iconType, verb } = classifyOp(op)
        const target = extractArgObject(op.argsText)
        // HITL 提问行：行标题 = 第一题问题文本（原始 args 是 questions JSON，
        // 不做通用摘要）；回答到达后展示用户答案（answer 子行）。
        const askHitl = iconType === 'ask_user' ? parseAskUserInteraction(op) : null
        rows.push({
          key: op.callId,
          kind: 'operation',
          // 两段式标题（设计图标准）：深色动词前缀 + 灰色目标摘要。
          // 所有操作行统一——此前只有联网检索行带 label，普通操作行
          // 整行落到灰色，视觉上「有的深有的浅」。
          label: verb,
          text: askHitl ? askHitl.question : opRowDetail(verb, iconType, target),
          iconType,
          nodeStatus: op.status,
          ops: [op],
          target: (askHitl?.question ?? target) || undefined,
          answer: askHitl?.answer ?? undefined,
        })
      }
    }
  }
  return rows
}

/** 折叠头汇总：纯联网检索 → 「搜索了网络」（设计图标准）；
 *  其余组合 → 「执行过程，读取了N个文件（，编辑了M个文件）（，执行了K个操作）」 */
export function buildTimelineSummary(rows: StepRow[]): TimelineSummary {
  let readCount = 0
  let editCount = 0
  let otherOpCount = 0
  let reasoningCount = 0
  let webSearchCount = 0
  for (const r of rows) {
    if (r.kind === 'reasoning') { reasoningCount++; continue }
    if (r.iconType === 'file_read') readCount++
    else if (r.iconType === 'file_edit') editCount++
    else if (r.iconType === 'web_search') webSearchCount++
    else otherOpCount++
  }
  let label = '执行过程'
  if (readCount === 0 && editCount === 0 && otherOpCount === 0 && webSearchCount > 0) {
    label = '搜索了网络'
  } else {
    if (readCount > 0) label += `，读取了${readCount}个文件`
    if (editCount > 0) label += `，编辑了${editCount}个文件`
    if (otherOpCount > 0) label += `，执行了${otherOpCount}个操作`
  }
  return { readCount, editCount, otherOpCount, reasoningCount, label }
}
