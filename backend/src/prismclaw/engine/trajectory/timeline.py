"""执行时间线（Execution Timeline）后端数据模型与投影器。

设计对齐 `docs/zh/execution-timeline-refactor-plan.md` §5.2 / §5.3 / §5.4：

- ``OperationGroup`` 是语义合并后的操作组（合并键 ``(step_id, kind, normalized_target)``），
  合并是**折叠不是丢弃**——``operations`` 明细（时间戳 / 状态 / 错误）全保留，可下钻。
- ``TimelineNode`` 是前端渲染的一级单元：``reasoning`` / ``step`` / ``orphan_operations``。
- ``TaskExecutionProjector`` 是有状态投影器，消费 ``ToolCallStart`` / ``ToolCallEnd`` 事件，
  内聚全部归属规则（§5.4 五种边界），产出 nodes 与增量 patch。

所有模型用显式 camelCase ``alias``，``model_dump(by_alias=True)`` 输出与前端 TS 类型逐字段一致
（见 P1.5 冻结约定）。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from prismclaw.engine.toolkit.registry.tool_classification import is_search_intent_tool

# --------------------------------------------------------------------------- #
# 底层明细与合并组
# --------------------------------------------------------------------------- #


class OperationDetail(BaseModel):
    """单次工具调用的明细（合并组 ``operations`` 内的逐条记录）。

    除状态/时间外，携带工具名、格式化参数与结果文本，以及归属的 iteration
    segmentId（供前端按「思考 → 操作」逐轮时序展示）。
    """

    model_config = ConfigDict(populate_by_name=True)

    call_id: str = Field(alias="callId")
    segment_id: int = Field(alias="segmentId")
    status: Literal["running", "done", "error"] = Field(alias="status")
    started_at: str = Field(alias="startedAt")
    ended_at: str | None = Field(default=None, alias="endedAt")
    error: str | None = Field(default=None, alias="error")
    tool_name: str | None = Field(default=None, alias="toolName")
    args_text: str | None = Field(default=None, alias="argsText")
    output: str | None = Field(default=None, alias="output")


# --------------------------------------------------------------------------- #
# Timeline 节点（前端一级单元）
# --------------------------------------------------------------------------- #


class IterationUnit(BaseModel):
    """单个 iteration 轮：一段思考 + 该轮自然语言输出 + 该轮触发的扁平操作列表。

    UI 展开后按 segmentId 升序渲染：每轮先展示 ``reasoning``（若有），
    再渲染 ``text``（若有，该轮自然语言输出 / 中间草稿 / 迭代过渡语），
    再渲染 ``operations``（扁平 OperationDetail[]，按 startedAt 升序）。

    扁平化设计（v5 重构）：不再按 (step_id, segment_id, kind, target) 折叠为
    OperationGroup — 那是早期设计的无效抽象（后端折叠后前端 buildStepRows 又拆回
    OperationDetail 列表，形成「折叠 → 展开」的无效往返）。扁平化后单一排序基准
    （startedAt）、merge 简单（按 call_id upsert）、patch 协议简化（不再有
    UpsertGroupPatch）。
    """

    model_config = ConfigDict(populate_by_name=True)

    segment_id: int = Field(alias="segmentId")
    reasoning: str = Field(default="", alias="reasoning")
    # 该轮自然语言输出（TextChunk 累积，不含工具参数/结果）。
    # 执行过程区域展示的"让我先分析一下..."等迭代过话语，正文区不再重复。
    text: str = Field(default="", alias="text")
    # 扁平 OperationDetail 列表，按 startedAt 升序。
    # 不再按 (step_id, segment_id, kind, target) 折叠为 OperationGroup —
    # OperationGroup 是早期设计的无效抽象：后端折叠后前端 buildStepRows 又拆回
    # OperationDetail 列表，形成「折叠 → 展开」的无效往返。扁平化后：
    # 排序单一（startedAt）、merge 简单（按 call_id upsert）、patch 协议简化
    # （不再需要 UpsertGroupPatch）。
    operations: list[OperationDetail] = Field(default_factory=list, alias="operations")


class StepNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["step"] = Field(default="step", alias="type")
    step_id: str | None = Field(default=None, alias="stepId")
    index: int = Field(default=0, alias="index")
    title: str = Field(default="", alias="title")
    status: Literal["completed", "current", "pending", "failed", "superseded"] = Field(
        default="pending", alias="status"
    )
    iterations: list[IterationUnit] = Field(default_factory=list, alias="iterations")
    revision_id: str | None = Field(default=None, alias="revisionId")
    # 批判性评估字段（P2.5 修复）
    critical_assessment: str | None = Field(default=None, alias="criticalAssessment")
    risk_points: list[str] = Field(default_factory=list, alias="riskPoints")
    unresolved_items: list[str] = Field(default_factory=list, alias="unresolvedItems")
    deviation_from_expected: str | None = Field(default=None, alias="deviationFromExpected")


class OrphanOperationsNode(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: Literal["orphan_operations"] = Field(default="orphan_operations", alias="type")
    iterations: list[IterationUnit] = Field(default_factory=list, alias="iterations")


class ReasoningNode(BaseModel):
    """模型推理过程的独立节点（兼容/兜底）。

    正常流程下 reasoning 不再以独立节点存在：每段推理内嵌到触发该段推理的 step
    / orphan 卡片内部。仅当某段推理没有任何关联工具调用（纯分析/最终总结）时
    才可能以独立节点呈现，由 Projector 按兜底策略产出。
    """

    model_config = ConfigDict(populate_by_name=True)

    type: Literal["reasoning"] = Field(default="reasoning", alias="type")
    segment_id: int = Field(default=0, alias="segmentId")
    content: str = Field(default="", alias="content")


TimelineNode = StepNode | OrphanOperationsNode | ReasoningNode


# --------------------------------------------------------------------------- #
# 增量 patch 与心跳载荷
# --------------------------------------------------------------------------- #


class HeartbeatPayload(BaseModel):
    """心跳帧载荷，与 P0.2 的 ``HeartbeatEvent`` 字段对齐（P1.4 迁入 timeline patch）。"""

    model_config = ConfigDict(populate_by_name=True)

    state: Literal["generating", "executing", "compacting", "waiting_llm", "awaiting_clarification"] = Field(alias="state")
    partial_tool_name: str | None = Field(default=None, alias="partialToolName")
    chars_so_far: int | None = Field(default=None, alias="charsSoFar")
    elapsed_s: float = Field(alias="elapsedS")


class UpsertNodePatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    op: Literal["upsert_node"] = Field(default="upsert_node", alias="op")
    node: TimelineNode = Field(alias="node")


class SetPhasePatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    op: Literal["set_phase"] = Field(default="set_phase", alias="op")
    phase: HeartbeatPayload = Field(alias="phase")


class IterationBoundaryPatch(BaseModel):
    """迭代边界 flush：告诉前端「上一轮迭代已完整推完，清空正文流式缓存」。

    后端 IterationBoundary(llm_start) 时发，携带上一轮的 segmentId。
    前端收到后把 streamingText 清空（上一轮自然语言输出已累积进 projector，
    会随下一次 timeline snapshot 一起推到 ExecutionTimeline），正文区只显示
    当前迭代吐字。Done 落库时 final_message 只保留最后一轮最终答案。
    """

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["iteration_boundary"] = Field(default="iteration_boundary", alias="op")
    segment_id: int = Field(alias="segmentId")


TimelinePatch = UpsertNodePatch | SetPhasePatch | IterationBoundaryPatch


# --------------------------------------------------------------------------- #
# 投影器
# --------------------------------------------------------------------------- #

_WRITE_TOOLS = {"write", "edit", "multiedit", "createfile"}
_READ_TOOLS = {"read", "readfile", "read_office", "read_image"}
_SEARCH_TOOLS = {
    "glob",
    "grep",
    "ripgrep",
    "search",
    "find",
    "web_search",  # legacy builtin (historical sessions)
}


def _is_search_tool(low: str) -> bool:
    """Search-kind detection: builtin names + configured web-retrieval tools."""
    return low in _SEARCH_TOOLS or is_search_intent_tool(low)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_args_text(arguments: dict[str, Any] | None) -> str | None:
    """把工具参数格式化为人类可读文本：每参数一行 ``key: value``，多行字符串值换行缩排展示。"""
    if not arguments:
        return None

    lines: list[str] = []
    for key, value in arguments.items():
        if isinstance(value, str):
            if "\n" in value:
                lines.append(f"{key}:\n{value}")
            else:
                lines.append(f"{key}: {value}")
        else:
            try:
                lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
            except (TypeError, ValueError):
                lines.append(f"{key}: {value!r}")
    return "\n".join(lines)


def _kind_for_tool(name: str) -> str:
    low = (name or "").lower()
    if low in _WRITE_TOOLS:
        return "write"
    if low in _READ_TOOLS:
        return "read"
    if _is_search_tool(low):
        return "search"
    if low == "bash":
        return "bash"
    if low == "skill":
        return "skill"
    if low == "agent":
        return "agent"
    return "tool"


def _rel_path(path: str, workspace_root: str | None) -> str:
    s = str(path)
    if workspace_root and s.startswith(workspace_root):
        rel = s[len(workspace_root):].lstrip("/")
        return rel or s
    return s


def _normalize_target(name: str, arguments: dict[str, Any] | None, workspace_root: str | None) -> str:
    """§5.2 ``normalized_target`` 规则：文件路径取 workspace 相对路径；bash 取命令首词；
    skill/agent 取名字/描述；其余尽量取路径，否则回退工具名。"""
    args = arguments or {}
    low = (name or "").lower()
    if low in _WRITE_TOOLS | _READ_TOOLS:
        p = args.get("file_path") or args.get("path") or args.get("file") or ""
        return _rel_path(p, workspace_root)
    if _is_search_tool(low):
        pat = args.get("pattern") or args.get("path") or name
        return str(pat)
    if low == "bash":
        cmd = args.get("command", "") or ""
        toks = cmd.strip().split()
        return toks[0] if toks else "bash"
    if low == "skill":
        return str(args.get("skill") or name)
    if low == "agent":
        desc = args.get("description") or args.get("name") or name
        return str(desc)[:80]
    # 兜底：尝试任意路径类参数，否则工具名
    p = args.get("file_path") or args.get("path") or args.get("command") or ""
    if p:
        return _rel_path(p, workspace_root)
    return name


def _extract_error_text(result: Any) -> str | None:
    """从 ``ToolResult.content`` 提取错误文本（不依赖 chat.py，保持模型自包含）。"""
    if result is None:
        return None
    content = getattr(result, "content", None)
    if not content:
        return None
    parts: list[str] = []
    for block in content:
        t = getattr(block, "text", None)
        if t is not None:
            parts.append(t)
        elif isinstance(block, dict) and "text" in block:
            parts.append(block["text"])
    return "\n".join(parts) or None


def _aggregate_status(statuses: list[str]) -> Literal["running", "done", "error", "partial_error"]:
    """§5.2 聚合：任一 running → running；全 done → done；含 error 但亦有 done → partial_error；
    全 error → error。"""
    if any(s == "running" for s in statuses):
        return "running"
    if any(s == "error" for s in statuses):
        return "partial_error" if any(s == "done" for s in statuses) else "error"
    return "done"


class TaskExecutionProjector:
    """有状态投影器：每会话执行一个实例，消费工具事件，产出 timeline nodes 与增量 patch。

    归属四规则（§5.4）+ 推理分段规则（方案 A'）全部内聚于此：
    1. 跨步骤：操作归属**启动时**的 ``step_id``；End 事件按 ``call_id`` 找回原 group。
    2. subagent：子调用不进主 timeline；Agent 工具本身作为一个 group。
    3. 直接执行（无 plan）：单一 ``orphan_operations`` 节点，group 内明细保留时间戳排序。
    4. 无 step_id 的工具：归入 ``orphan_operations``。
    5. reasoning 分段：由 ``IterationBoundary`` 切段，每段 reasoning 在首次 ToolCallStart
       时嵌入到所属 step / orphan 卡片的 ``reasoningSegments``，不再产出独立 ReasoningNode。
    """

    def __init__(self, workspace_root: str | None = None) -> None:
        self._workspace_root = workspace_root
        # 扁平操作列表：所有 ToolCallStart/End 都 upsert 到这里，按 call_id 定位。
        # 不再按 (step_id, segment_id, kind, target) 折叠为 OperationGroup —
        # OperationGroup 是早期设计的无效抽象（见 IterationUnit.operations 字段注释）。
        self._operations: list[OperationDetail] = []
        # call_id -> index in self._operations（O(1) upsert）
        self._op_index: dict[str, int] = {}
        # step_id / None(orphan) -> set[call_id]（归类 step/orphan 节点）
        self._step_ops: dict[str | None, set[str]] = {}
        # call_id -> (segment_id, step_id)，标记该工具归哪段的哪张卡片
        self._tool_segment: dict[str, tuple[int, str | None]] = {}
        # 按 step_id / None(orphan) -> set[segment_id] 记录哪些段已经附过 reasoning
        self._segments_attached: dict[str | None, set[int]] = {}
        # step_id / None(orphan) -> {segment_id -> content}（已嵌入到卡片的 reasoningSegments）
        self._node_reasoning_segments: dict[str | None, dict[int, str]] = {}
        # 推理内容：segment_id -> content 累积
        self._current_segment: int = 0
        self._segment_reasonings: dict[int, str] = {}
        # 每轮迭代的自然语言输出：segment_id -> text 累积
        self._segment_texts: dict[int, str] = {}
        self._message_id: str | None = None

    @property
    def current_segment(self) -> int:
        """公开访问当前迭代段（segment_id）。

        用于 chat_runner 在推 SERVER_CHAT_DELTA 时把 segment_id 塞进 payload，
        让前端能在 segment 切换时（下一轮迭代的第一个 TextChunk 到达）
        才清空 streamingText — 避免 IterationBoundary 提前清空导致的空白窗口。
        """
        return self._current_segment

    # -- 公共查询 ----------------------------------------------------------- #

    def has_content(self) -> bool:
        """门控：本次执行是否有 reasoning / text 段 / ≥1 operation。"""
        has_segment = any(seg.strip() for seg in self._segment_reasonings.values())
        has_text = any(txt.strip() for txt in self._segment_texts.values())
        return has_segment or has_text or bool(self._operations)

    def set_message_id(self, message_id: str | None) -> None:
        self._message_id = message_id

    # -- 分段信号 ----------------------------------------------------------- #

    def on_iteration_boundary(self, boundary: Any) -> UpsertNodePatch | None:
        """处理 IterationBoundary：llm_start 切换当前段；llm_end 结束当前段的 spinner。
        当前实现只消费 llm_start（切段）；llm_end 作为可选标记不返回 patch（随下一次工具
        执行 patch 会自然把 reasoning 定下来）。"""
        if getattr(boundary, "kind", "llm_start") == "llm_start":
            seg = int(boundary.iteration)
            self._current_segment = seg
            if seg not in self._segment_reasonings:
                self._segment_reasonings[seg] = ""
            if seg not in self._segment_texts:
                self._segment_texts[seg] = ""
            # 无需 patch：reasoning 内容变化时由 reasoning_patch 推送，段切换本身不引起 UI 变更
        return None

    def on_iteration_text(self, text: str) -> None:
        """把 TextChunk 累积进当前 segment 的自然语言输出。

        runner 在每个 TextChunk 事件里调这个方法，把每轮迭代的自然语言输出
        （包括中间草稿、迭代过渡语、最终答案）都收集到 _segment_texts[seg]。
        最终 build_snapshot_dict / build_step_node 产出的 IterationUnit.text
        字段承载这段内容，ExecutionTimeline 展示时按 segmentId 逐轮渲染。
        这样完整的迭代过程就进了执行时间线，正文区只落库最后一轮最终答案。
        """
        if not text:
            return
        seg = self._current_segment
        if seg not in self._segment_texts:
            self._segment_texts[seg] = ""
        self._segment_texts[seg] += text

    # -- 工具事件 ----------------------------------------------------------- #

    def _upsert_reasoning_segment_to_card(
        self, step_id: str | None, seg_id: int, content: str
    ) -> None:
        """把 reasoning 段附加到 step/orphan 的 reasoningSegments（按 seg_id 去重）。"""
        bucket = self._segments_attached.setdefault(step_id, set())
        if seg_id in bucket:
            return
        bucket.add(seg_id)
        # 内容写入对应节点的字段：build_step_node / to_nodes 会读 _node_reasoning_segments
        store = self._node_reasoning_segments.setdefault(step_id, {})
        store[seg_id] = content

    def on_tool_call_start(
        self, event: Any, workspace_root: str | None = None
    ) -> list[TimelinePatch]:
        step_id = event.step_id
        seg = self._current_segment

        # 工具归当前段：记录 segment_id + step_id（用于后续 reasoning 附属）
        self._tool_segment[event.tool_call_id] = (seg, step_id)
        # 归类到 step/orphan 节点的 call_id 集合（build_iterations 时用）
        self._step_ops.setdefault(step_id, set()).add(event.tool_call_id)

        # 若当前段已有 reasoning 内容且这张卡片（step/orphan）还没附过该段 reasoning → 附加
        seg_content = self._segment_reasonings.get(seg, "").strip()
        newly_attached = False
        if seg_content:
            bucket = self._segments_attached.get(step_id, set())
            if seg not in bucket:
                newly_attached = True
            self._upsert_reasoning_segment_to_card(step_id, seg, seg_content)

        # 扁平化：直接 append 到 _operations，按 call_id upsert（O(1)）。
        detail = OperationDetail(
            call_id=event.tool_call_id,
            segmentId=seg,
            status="running",
            started_at=_now_iso(),
            tool_name=event.name,
            args_text=_format_args_text(event.arguments),
        )
        if event.tool_call_id in self._op_index:
            self._operations[self._op_index[event.tool_call_id]] = detail
        else:
            self._op_index[event.tool_call_id] = len(self._operations)
            self._operations.append(detail)

        # 不再推 UpsertGroupPatch — 只推完整 rebuild_snapshot 的 UpsertNodePatch。
        # 扁平结构下 patch 协议简化：后端只发 upsert_node（完整快照替换），
        # 前端不再需要 mergeIterations / upsertGroup 等增量合并逻辑。
        patches: list[TimelinePatch] = []
        if step_id is not None:
            patches.append(UpsertNodePatch(node=self._build_step_node(step_id)))
        else:
            patches.append(UpsertNodePatch(node=self._build_orphan_node()))
        # 段从"独立 reasoning 节点"晋升到卡片 → 消掉前端已渲染的独立节点。
        if newly_attached:
            patches.append(
                UpsertNodePatch(node=ReasoningNode(segmentId=seg, content=""))
            )
        return patches

    def on_tool_call_end(self, event: Any, workspace_root: str | None = None) -> list[TimelinePatch] | None:
        # 扁平化：直接按 call_id 在 _operations 里定位
        idx = self._op_index.get(event.tool_call_id)
        if idx is None:
            # 起始事件缺失（理论上同循环内 start 必先于 end）；安全跳过。
            return None

        detail = self._operations[idx]
        is_error = bool(getattr(event.result, "is_error", False))
        detail.status = "error" if is_error else "done"
        detail.ended_at = _now_iso()
        # 成功与失败均保留完整结果文本（前端限高滚动展示；持久化完整落库，
        # 截断只允许存在于展示层，不允许存在于数据层）；error 字段单独承载错误全文。
        detail.output = _extract_error_text(event.result)
        if is_error:
            detail.error = _extract_error_text(event.result)

        # 找到 step_id（通过 _tool_segment）
        seg, step_id = self._tool_segment.get(event.tool_call_id, (self._current_segment, None))

        # 不再推 UpsertGroupPatch — 只推完整 rebuild_snapshot 的 UpsertNodePatch
        if step_id is not None:
            return [UpsertNodePatch(node=self._build_step_node(step_id))]
        return [UpsertNodePatch(node=self._build_orphan_node())]

    # -- 推理事件 ----------------------------------------------------------- #

    def on_reasoning_delta(self, delta: str) -> None:
        """累积当前段的推理内容（``ReasoningChunk.reasoning``）。

        仅维护状态，不返回 patch——reasoning 是连续 token 流，推送节流由调用方
        （``chat.py``）按时间窗口驱动，避免逐 token 刷帧。调用方通过
        :meth:`reasoning_patch` 取所属卡片的 upsert patch（当前段内容已嵌入的卡片）。

        **First-content attach（根因修复）**：生产真实事件顺序往往是
        ``ToolCallStart`` 先到达（此时 seg 内 reasoning 仍为空 → 不附加到 card）、
        reasoning 流式 token 后到达。原同步逻辑（只遍历 ``_node_reasoning_segments``
        中已有的 seg entry）会导致 reasoning **永远挂不到 card**，最终
        ``iteration.reasoning`` 全空 → 前端 ``isReadOnlyTimeline()`` 因找不到 reasoning
        判为轻量，静默 collapse 思考内容。修复：在 reasoning 从"空"首次跨过"有内容"
        阈值时，扫描所有 ``_tool_segment`` 中归属于当前段的 card，执行
        ``_upsert_reasoning_segment_to_card`` 把 entry 正式写入 store，后续的
        增量更新同步 for-loop 就能自然命中 entry 了。
        """
        if delta:
            seg = self._current_segment
            prev_content = self._segment_reasonings.get(seg, "")
            new_content = prev_content + delta
            self._segment_reasonings[seg] = new_content

            # — 首次跨过非空阈值 → 把当前段 reasoning 正式附加到所有关联卡片 —
            if not prev_content.strip() and new_content.strip():
                for _call_id, (tc_seg, step_id) in self._tool_segment.items():
                    if tc_seg != seg:
                        continue
                    # 去重/幂等：_upsert_reasoning_segment_to_card 内部以 seg_id 做
                    # bucket 去重，多次调用安全。
                    self._upsert_reasoning_segment_to_card(
                        step_id, seg, new_content
                    )
                # 后续 reasoning_patch() 会以 card 内嵌形式 upsert 给前端，
                # 同时把 seg 从"独立 ReasoningNode 兜底"晋升为 card 内嵌形态。

            # 已有 card-attached entry：保持增量同步（原逻辑不变）
            for card_key, store in self._node_reasoning_segments.items():
                if seg in store:
                    store[seg] = new_content

    def reasoning_patch(self) -> UpsertNodePatch | None:
        """构建当前段对应的 upsert_node patch：
        - 若当前段已附着到某张 step/orphan 卡片 → 返回该卡片（内嵌 reasoningSegments）。
        - 若尚未附着任何工具（纯推理 streaming 中）→ 兜底返回独立 ReasoningNode patch，
          保证 streaming 期间 UI 有更新；后续 ToolCallStart 会把这段 reasoning 正式迁移
          到卡片内部、独立 reasoning 节点被同 segmentId 的内容覆盖（前端 merge 会去重）。
        - 空内容返回 None。
        """
        seg_content = self._segment_reasonings.get(self._current_segment, "")
        if not seg_content.strip():
            return None
        # 已附着卡片：优先返回 step/orphan 内嵌形态
        for step_id, segs in self._node_reasoning_segments.items():
            if self._current_segment in segs:
                if step_id is None:
                    # orphan 卡片有内容（有操作或有推理段）才构建
                    if self._step_ops.get(None) or self._node_reasoning_segments.get(None):
                        return UpsertNodePatch(node=self._build_orphan_node())
                else:
                    return UpsertNodePatch(node=self._build_step_node(step_id))
                break
        # 未附着：独立 ReasoningNode（兜底，streaming 时必须有 UI）
        return UpsertNodePatch(
            node=ReasoningNode(
                segmentId=self._current_segment,
                content=seg_content,
            )
        )

    # -- 节点产出 ----------------------------------------------------------- #

    def _build_iterations(
        self,
        *,
        step_id_filter: str | None,
        seg_store: dict[int, str],
    ) -> list[IterationUnit]:
        """按 step/orphan 过滤的扁平 OperationDetail 列表，按 segmentId 聚合。

        segmentId 取 seg_store、过滤后的 operations、_segment_texts 三者并集，升序输出。
        同一个 segmentId 内的 OperationDetail 按 startedAt 升序（单一排序基准，不再有
        OperationGroup 折叠/展开往返造成的"乱窜"）。
        """
        filtered_ops = [
            op for op in self._operations
            if self._op_index.get(op.call_id) is not None
            and self._tool_segment.get(op.call_id, (None, None))[1] == step_id_filter
        ]

        segment_ids: set[int] = set(seg_store.keys())
        for op in filtered_ops:
            segment_ids.add(op.segment_id)
        # 只有 text 的 segment 也保留（纯自然语言过语）
        for sid in self._segment_texts:
            segment_ids.add(sid)

        # 按 segmentId 聚合 + startedAt 排序（扁平，不再有 group 层）
        by_seg: dict[int, list[OperationDetail]] = {}
        for op in filtered_ops:
            by_seg.setdefault(op.segment_id, []).append(op)

        out: list[IterationUnit] = []
        for sid in sorted(segment_ids):
            reasoning = seg_store.get(sid, "") or ""
            ops = sorted(
                by_seg.get(sid, []),
                key=lambda op: (
                    datetime.fromisoformat(op.started_at.replace("Z", "+00:00")).timestamp()
                    if op.started_at
                    else 0.0
                ),
            )
            text = self._segment_texts.get(sid, "") or ""
            # 保留至少有 reasoning / text 内容或至少一个操作的轮次
            if not reasoning.strip() and not text.strip() and not ops:
                continue
            out.append(
                IterationUnit(
                    segmentId=sid,
                    reasoning=reasoning,
                    text=text,
                    operations=ops,
                )
            )
        return out

    def _build_orphan_node(self) -> OrphanOperationsNode:
        seg_store = self._node_reasoning_segments.get(None, {})
        return OrphanOperationsNode(
            iterations=self._build_iterations(step_id_filter=None, seg_store=seg_store),
        )

    def build_snapshot_dict(self, assistant_message_id: str | None) -> dict:
        """完整 timeline 快照，供 RunProjection 持久化 + 前端 hydrateRun 重建。

        扁平重构后结构简化：messageId + nodes。nodes 里的 OrphanOperationsNode /
        StepNode 已经自带 segments + iterations，不再需要单独的 boundaries 字段。
        节点是 Pydantic model 实例，必须 by_alias=True 才能转成前端期望的 camelCase
        dict（timeline_nodes 类型定义 list[dict]）。
        """
        return {
            "messageId": assistant_message_id,
            "nodes": [n.model_dump(by_alias=True) for n in self.to_nodes()],
        }

    def to_nodes(self) -> list[TimelineNode]:
        # step 节点（扁平结构下直接从 _step_ops 的 key 集合取）
        step_ids = [sid for sid in self._step_ops.keys() if sid is not None]
        step_nodes: list[TimelineNode] = [
            self._build_step_node(step_id) for step_id in sorted(step_ids)
        ]
        # orphan 节点（_step_ops 里 step_id=None 且有内容）
        orphan_nodes: list[TimelineNode] = []
        has_orphan_ops = bool(self._step_ops.get(None))
        has_orphan_reasoning = bool(self._node_reasoning_segments.get(None))
        if has_orphan_ops or has_orphan_reasoning:
            orphan_nodes = [self._build_orphan_node()]
        # 兜底：没关联到任何工具的 reasoning 段作为独立节点（纯分析/最终总结）。
        attached: set[int] = set()
        for store in self._node_reasoning_segments.values():
            attached.update(store.keys())
        orphan_segs = [
            (sid, text)
            for sid, text in sorted(self._segment_reasonings.items())
            if sid not in attached and text.strip()
        ]
        reasoning_nodes: list[TimelineNode] = [
            ReasoningNode(segmentId=sid, content=text) for sid, text in orphan_segs
        ]
        # 关键修复：不再把独立 reasoning 无条件甩到末尾，而是按真实时间序
        # （segmentId）交错插入到 step / orphan 之间。否则纯推理段（如发生在
        # 两个执行步骤之间的"换思路"思考）会被错误排到所有执行之后，造成
        # 「思考与执行时序颠倒」的观感。time_key 用节点承载的最早 segmentId。
        # tie-break：同 segment 时自由节点（reasoning）排在卡片之前——
        # 段内时序固定为「思考 → 工具调用」，消除快照帧（自愈/历史回放）
        # 与增量帧的顺序翻转跳变。
        def time_key(n: TimelineNode) -> tuple[int, int]:
            if n.type == "reasoning":
                return (n.segment_id if n.segment_id is not None else -1, 0)
            segs = [it.segment_id for it in n.iterations]
            return (min(segs) if segs else 0, 1)

        all_nodes = step_nodes + orphan_nodes + reasoning_nodes
        all_nodes.sort(key=time_key)
        return all_nodes

    def _derive_step_status(self, step_id: str) -> Literal["completed", "current", "pending", "failed"]:
        """根据扁平 operations 的运行状态自动推导步骤状态。"""
        calls = self._step_ops.get(step_id, set())
        all_details = [
            self._operations[idx]
            for call_id in calls
            if (idx := self._op_index.get(call_id)) is not None
        ]
        if not all_details:
            return "pending"
        has_running = any(d.status == "running" for d in all_details)
        if has_running:
            return "current"
        has_error = any(d.status == "error" for d in all_details)
        if has_error:
            return "failed"
        return "completed"

    def _compute_critical_assessment(self, step_id: str) -> dict[str, Any]:
        """为步骤计算批判性评估信息（扁平版本）。"""
        calls = self._step_ops.get(step_id, set())
        all_details = [
            self._operations[idx]
            for call_id in calls
            if (idx := self._op_index.get(call_id)) is not None
        ]
        if not all_details:
            return {}
        total = len(all_details)
        completed = sum(1 for d in all_details if d.status == "done")
        failed = sum(1 for d in all_details if d.status == "error")
        running = sum(1 for d in all_details if d.status == "running")
        success_rate = completed / total if total > 0 else 0.0

        # 风险点识别
        risk_points: list[str] = []
        if failed > 0:
            risk_points.append(f"有 {failed} 个操作失败")
        if running > 0:
            risk_points.append(f"有 {running} 个操作正在执行")
        if total > 3 and success_rate < 0.8:
            risk_points.append(f"成功率 {success_rate:.0%} 低于 80%")

        # 未解决项
        unresolved_items: list[str] = []
        for detail in all_details:
            if detail.status == "error":
                tool_name = detail.tool_name or "未知工具"
                error_msg = detail.error or "未知错误"
                unresolved_items.append(f"{tool_name} 失败：{error_msg[:100]}")

        # 偏差评估
        deviation_from_expected: str | None = None
        if failed > 0:
            deviation_from_expected = f"有 {failed} 个操作失败，可能影响任务完成"
        elif total > 5 and success_rate < 0.9:
            deviation_from_expected = f"成功率 {success_rate:.0%} 低于预期，可能存在系统性问题"

        return {
            "success_rate": success_rate,
            "risk_points": risk_points,
            "unresolved_items": unresolved_items,
            "deviation_from_expected": deviation_from_expected,
        }

    def _build_step_node(self, step_id: str) -> StepNode:
        final_status = self._derive_step_status(step_id)

        # 批判性评估信息
        assessment = self._compute_critical_assessment(step_id)
        critical_assessment: str | None = None
        if assessment.get("risk_points") or assessment.get("unresolved_items"):
            parts: list[str] = []
            if assessment.get("risk_points"):
                parts.append("风险：" + "；".join(assessment["risk_points"]))
            if assessment.get("unresolved_items"):
                parts.append("未解决：" + "；".join(assessment["unresolved_items"]))
            critical_assessment = "。".join(parts)

        # 推理段 + 扁平操作 按 segmentId 合并为 iterations
        seg_store = self._node_reasoning_segments.get(step_id, {})
        iterations = self._build_iterations(step_id_filter=step_id, seg_store=seg_store)

        return StepNode(
            stepId=step_id,
            index=0,
            title=step_id or "",
            status=final_status,
            iterations=iterations,
            criticalAssessment=critical_assessment,
            riskPoints=assessment.get("risk_points", []),
            unresolvedItems=assessment.get("unresolved_items", []),
            deviationFromExpected=assessment.get("deviation_from_expected"),
        )
