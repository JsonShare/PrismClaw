import type { TimelineNode } from '../../types'

/* ─── 执行过程资源分组（L2 理解层） ──────────────────────────────────────────
 * 设计来源：/tmp/execution_process_design.md（信息分层模型）
 * 核心原则：按「资源类型」分组（文献 / 网页 / 操作），组内展示对象
 * （文献标题·来源·年份、网页标题·域名），而非工具调用记录。
 *
 * 真实工具清单（2026-06 核实）：
 *   web: 独立 MCP 检索服务的 search 工具（服务名是部署配置，
 *        按 server__search / server:search 名称模式识别）
 *   历史会话中可能出现已下线的内置名 web_search / web_fetch / web_extract。
 *   其余（bash/write/edit/read/glob/grep/skill_search/todo_write/agent_spawn…）
 *   一律归「操作」。后端无文献类 agent 工具（文献检索是 /api/medical
 *   页面级 REST 能力，不进 timeline）——literature 分支为未来 MCP /
 *   医疗工具预留的名称模式，当前常规会话不会命中。
 * ──────────────────────────────────────────────────────────────────────────── */

const LITERATURE_TOOL_RE = /literature|pubmed|guideline_search|drug_search/

/** 网页类工具：浏览/抓取外部网页（服务名是部署配置，按名称模式识别） */
function isWebTool(name: string): boolean {
  // 已下线内置名 web_*（历史会话兼容）由 startsWith('web') 覆盖；任意 MCP
  // 服务的 search 工具按两种名形态识别（单下划线的 memory_search /
  // skill_search 不命中）。
  return name.startsWith('web')
    || name.includes('__search')
    || name.includes(':search')
}

/** 只读（无副作用）工具白名单：文件/目录读取 + 各类检索（本地 / 网络 / 文献）。
 *  正向白名单 + 默认重路径：未被识别的写入 / 技能 / agent / 询问用户等一律
 *  不算只读，与 default-then-show-details 的安全姿态一致。 */
function isReadOnlyTool(name: string): boolean {
  const n = (name ?? '').toLowerCase()
  if (isWebTool(n)) return true
  if (LITERATURE_TOOL_RE.test(n)) return true
  // 文件 / 目录读取
  if (n === 'ls' || n === 'list_dir' || n === 'list_directory') return true
  if (n === 'glob' || n === 'find') return true
  if (n === 'grep' || n === 'rg') return true
  if (n.startsWith('read') || n === 'cat') return true
  // 检索类（本地 / 记忆），startsWith('read') 未覆盖的 read_* 走 read 分支，
  // 此处兜底 search / memory 语义
  if (n.includes('search') || n.includes('memory')) return true
  return false
}

/**
 * Whether a timeline is purely read-only (every operation is read/search —
 * side-effect-free) AND contains no reasoning text. Used by MessageBubble to
 * pick the ExecutionTimeline rendering weight:
 *   - pure read-only + no reasoning → light（紧凑内联，无完成行）
 *   - anything else                 → heavy（reasoning / 写入 / 技能 /
 *                                     agent → 完整步骤 + 完成行）
 *
 * 注意：判定结果只作为 weight 入参，不再用于切换组件（2026-09-03）。
 * 该判定在流式期间会翻转，组件级切换会替换 DOM 根节点并重播入场动画。
 *
 * Boundary rationale:
 *   1. Operations are classified using a positive-read-only whitelist. Write-class
 *      tools with unrecognized names (e.g. office_write, forget, scheduled_task)
 *      land in the default "tool" bucket and correctly fall through to the heavy
 *      view — a safe default-then-show-details stance.
 *   2. Reasoning content means the user wants to SEE the thinking process (the
 *      entire timeline design goal). Silently collapsing a reasoning-rich
 *      read-only session (e.g. a web research task: 3 searches + 2 paragraphs of
 *      reasoning in between) into "搜索了 3 次" is a content-loss bug, exactly the
 *      symptom reported: "第一张图刚思考完，第二张图内容就丢失了，只有搜索了几次".
 *
 * Returns false for empty/ops-less timelines or any timeline that carries
 * non-empty reasoning so they fall through to the heavy weight (which renders
 * reasoning inline without losing content).
 */
export function isReadOnlyTimeline(timeline: TimelineNode[] | undefined): boolean {
  if (!timeline || timeline.length === 0) return false
  let hasOps = false
  for (const node of timeline) {
    if (node.type === 'step' || node.type === 'orphan_operations') {
      for (const it of node.iterations) {
        if (it.reasoning?.trim()) return false
        for (const op of it.operations) {
          hasOps = true
          // 含失败操作 → 重路径：轻量摘要会吞掉失败，而重路径的「N 次失败」
          // 红字叙事正是医生需要的证据完整性信号（与 default-then-show-details
          // 的安全姿态一致）。
          if (op.status === 'error' || (op.error ?? null) != null) return false
          if (!isReadOnlyTool(op.toolName ?? '')) return false
        }
      }
    } else if (node.type === 'reasoning') {
      // 独立 reasoning 节点 → 一定不是 light：用户要看执行过程内容，
      // 折叠成"搜索了 N 次"属于内容丢失。
      return false
    }
  }
  return hasOps
}
