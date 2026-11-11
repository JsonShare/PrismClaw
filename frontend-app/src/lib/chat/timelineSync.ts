/**
 * Execution Timeline 前端同步（P3 — 无序号、快照自愈）。
 *
 * 设计要点（对齐后端 `task.timeline` / `task.runtime` / `run.snapshot` 下发）：
 *
 * - **快照帧**（`nodes` 非空）：整体替换，立即自愈。
 * - **patch 帧**：只有 upsert_node（后端只发完整 rebuild_snapshot，不再有 upsert_group）。
 *   OperationGroup 扁平化后 patch 协议大幅简化：后端每次 ToolCallStart/End 都推
 *   UpsertNodePatch（完整迭代快照，含扁平 OperationDetail[]），前端只需要按
 *   节点身份（nodeKey）替换 + iterations 按 segmentId 合并（callId upsert）。
 * - 断连/刷新由后端内存权威源（GET /run）+ hydrateRun 全量重建兜底。
 *
 * 本模块全部为纯函数。
 */

import type {
  TimelineNode,
  TimelinePatch,
  OperationDetail,
  IterationUnit,
} from '../../types'

/** 前端维护的 timeline 同步状态（存于 ConvState）。P3 起仅持 nodes，无序号光标。 */
export interface TimelineSyncState {
  /** 当前 nodes（快照替换 / 增量叠加结果）。 */
  nodes: TimelineNode[]
}

/**
 * 节点类型守卫：类型字段不在 TimelineNode 联合里的一律判为未知。
 *
 * 只应在持久化数据进入前端的边界调用一次（useSessionLoader 水合历史消息）。
 * 后端读取 timeline 时是原样 JSON 透传、无 model 校验，所以必须在客户端挡掉
 * 未知类型；渲染层按当前类型契约直接读字段，不做未知类型兜底，否则真实数据
 * 缺陷会被静默吞掉。
 */
export function isKnownTimelineNode(n: unknown): n is TimelineNode {
  if (!n || typeof n !== 'object') return false
  const t = (n as { type?: unknown }).type
  return t === 'reasoning' || t === 'step' || t === 'orphan_operations'
}

/** 一帧 `task.timeline` 的归一化载荷。 */
export interface TimelineFrame {
  /** 快照帧携带全量 nodes；patch 帧不带。 */
  nodes?: TimelineNode[]
  /** 增量 patch 列表；快照帧不带。 */
  patch?: TimelinePatch[]
}

/**
 * 在给定 nodes 上应用一组 patch，返回新数组（不可变）。
 * 扁平结构下只有 upsert_node / set_phase / iteration_boundary 三种：
 * - upsert_node：按 nodeKey 定位目标节点，iterations 按 segmentId + callId 合并
 * - set_phase / iteration_boundary：timelineSync 层不处理（store 层已消费）
 */
export function applyTimelinePatches(nodes: TimelineNode[], patches: TimelinePatch[]): TimelineNode[] {
  let result = nodes
  for (const patch of patches) {
    result = applyOnePatch(result, patch)
  }
  return result
}

function nodeKey(node: TimelineNode): string {
  if (node.type === 'reasoning') {
    return node.segmentId != null ? `reasoning:${node.segmentId}` : 'reasoning'
  }
  if (node.type === 'orphan_operations') return 'orphan_operations'
  return `step:${node.stepId ?? ''}`
}

/** 扁平 OperationDetail 按 startedAt 升序（单一排序基准）。 */
function sortOpsChrono(ops: OperationDetail[]): OperationDetail[] {
  return [...ops].sort((a, b) => {
    const ta = Date.parse(a.startedAt)
    const tb = Date.parse(b.startedAt)
    if (!Number.isNaN(ta) && !Number.isNaN(tb)) return ta - tb
    return 0
  })
}

/**
 * 按 segmentId 合并两组 iterations；同一 segmentId 内的 OperationDetail 按 callId upsert。
 *
 * 扁平化后不再有 OperationGroup 折叠层 — 后端每次 UpsertNodePatch 携带完整的
 * rebuild_snapshot（扁平 OperationDetail[]），前端只需按 callId 替换旧值。
 * 排序基准统一为 startedAt（后端 projector 和前端 timelineSync 完全对齐）。
 */
function mergeIterations(
  existing: IterationUnit[] | undefined,
  incoming: IterationUnit[] | undefined,
): IterationUnit[] {
  const bySeg = new Map<number, IterationUnit>()

  // 把现有 iterations 展平进 Map
  for (const u of existing ?? []) {
    bySeg.set(u.segmentId, {
      ...u,
      operations: [...u.operations],
    })
  }

  // 合并 incoming
  for (const u of incoming ?? []) {
    const prev = bySeg.get(u.segmentId)
    if (!prev) {
      bySeg.set(u.segmentId, {
        ...u,
        operations: sortOpsChrono([...u.operations]),
      })
      continue
    }
    // reasoning 以新内容为准；operations 按 callId 合并（新优先）
    const mergedOps = new Map<string, OperationDetail>()
    for (const op of prev.operations) mergedOps.set(op.callId, op)
    for (const op of u.operations) mergedOps.set(op.callId, op)
    // text 也以新内容为准（后端 projector 累积的完整文本会在下次 snapshot 携带）
    bySeg.set(u.segmentId, {
      segmentId: u.segmentId,
      reasoning: u.reasoning || prev.reasoning,
      text: u.text ?? prev.text,
      operations: sortOpsChrono(Array.from(mergedOps.values())),
    })
  }

  return Array.from(bySeg.values()).sort((a, b) => a.segmentId - b.segmentId)
}

function applyOnePatch(nodes: TimelineNode[], patch: TimelinePatch): TimelineNode[] {
  switch (patch.op) {
    case 'upsert_node':
      return upsertNode(nodes, patch.node)
    case 'set_phase':
      // phase 由 store 层消费（applyTimelineFrame 里单独处理），nodes 不受影响。
      return nodes
    case 'iteration_boundary':
      // 后端 IterationBoundary(llm_start) 时发：store 层清空 streamingText，
      // nodes 不受影响。
      return nodes
  }
}

/** 节点的真实时间序键（与后端 time_key 对齐的 tuple 排序）。
 *
 * 后端 time_key 返回 (segmentId, tier)：
 *   - tier=0：reasoning 自由节点（同段内排在卡片之前）
 *   - tier=1：step / orphan 卡片节点
 *
 * 前端必须与之严格一致——否则同 segmentId 的 reasoning 与 step 会因
 * 插入先后顺序产生「思考跑到工具卡片后面」的时序错乱。 */
function nodeTimeKey(node: TimelineNode): [number, number] {
  if (node.type === 'reasoning') {
    return [node.segmentId ?? -1, 0]
  }
  const segs = node.iterations?.map((it) => it.segmentId) ?? []
  if (segs.length) return [Math.min(...segs), 1]
  if (node.type === 'step') return [node.index ?? 0, 1]
  return [0, 1]
}

/** tuple 比较：a > b 当 a 在 b 之后（按 Python tuple 字典序）。 */
function tupleGT(a: [number, number], b: [number, number]): boolean {
  return a[0] > b[0] || (a[0] === b[0] && a[1] > b[1])
}

function upsertNode(nodes: TimelineNode[], node: TimelineNode): TimelineNode[] {
  const key = nodeKey(node)
  const idx = nodes.findIndex((n) => nodeKey(n) === key)
  if (idx === -1) {
    // 新节点：按 tuple 时间序定位插入位置
    const tk = nodeTimeKey(node)
    const insertIdx = nodes.findIndex((n) => tupleGT(nodeTimeKey(n), tk))
    return insertIdx === -1 ? [...nodes, node] : [...nodes.slice(0, insertIdx), node, ...nodes.slice(insertIdx)]
  }

  const existing = nodes[idx]
  // 合并策略：iterations 按 segmentId + callId 合并（扁平结构，单一排序基准 startedAt）
  let merged: TimelineNode
  if (node.type === 'step' && existing.type === 'step') {
    merged = { ...node, iterations: mergeIterations(existing.iterations, node.iterations) }
  } else if (node.type === 'orphan_operations' && existing.type === 'orphan_operations') {
    merged = { ...node, iterations: mergeIterations(existing.iterations, node.iterations) }
  } else {
    merged = node
  }
  const next = nodes.slice()
  next[idx] = merged
  return next
}

/**
 * 计算应用一帧后的新同步状态（纯函数）。
 *
 * - 快照帧（`nodes` 非空）：全量替换，立即自愈。空快照协议层非法 → no-op。
 * - patch 帧：best-effort 增量合并；乱序/丢帧交给下一次快照帧整体覆盖纠正。
 */
export function nextTimelineState(state: TimelineSyncState, frame: TimelineFrame): TimelineSyncState {
  const { nodes, patch } = frame

  // 快照帧：全量替换，立即自愈。
  if (nodes !== undefined) {
    // 空快照（nodes: [] / null）在协议层非法：不得清空已累积的执行过程。
    // 真实清空由 chat.start 的 reset 承担，空快照一律 no-op。
    if (!nodes || nodes.length === 0) {
      return state
    }
    return { nodes }
  }

  // 增量 patch 帧：best-effort 合并（无序号校验）。
  return {
    nodes: applyTimelinePatches(state.nodes, patch ?? []),
  }
}
