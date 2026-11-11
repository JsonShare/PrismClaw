# 审查系统

## 职责概述

审查系统（Review System）是 PrismClaw 的文件变更审查基础设施，为 AI 代理产生的所有文件编辑提供基线快照、变更追踪、用户确认/驳回和原子回滚能力。核心职责包括：

- **基线快照**：首次编辑时自动捕获文件原始内容（content-addressable blob）
- **变更追踪**：以 append-only 事件流记录所有编辑、决策和物化操作
- **文件格式策略**：统一审查合约 `ReviewableChange`，按格式分派不同策略（text/code / xlsx / pptx / docx）
- **原子回滚**：通过 `materializer` 的 temp+fsync+os.replace 保证文件写入原子性
- **一致性校验**：reconcile 机制检测 out-of-band 修改，防止盲目覆盖
- **Blob GC**：基于引用计数的垃圾回收，安全删除未被任何 ledger 引用的旧 blob

## 架构总览

**[查看交互式架构图](../diagrams/14-review-system.html)**

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          审查系统模块总览                                 │
│                                                                          │
│  ┌─────────────────────────────────────────────────────────────────┐    │
│  │                     dispatch(fmt) → 策略派发                    │    │
│  │  ┌──────────────┐  ┌──────────────────┐  ┌──────────────┐      │    │
│  │  │  TextChange  │  │ SpreadsheetChange│  │  DocxChange  │      │    │
│  │  │ (text/code)  │  │   (xlsx/pptx)    │  │    (docx)    │      │    │
│  │  └──────┬───────┘  └────────┬─────────┘  └──────┬───────┘      │    │
│  └─────────┼───────────────────┼───────────────────┼───────────────┘    │
│            │                   │                   │                    │
│            ▼                   ▼                   ▼                    │
│  ┌─────────────────┐  ┌───────────────┐  ┌────────────────────┐        │
│  │ ReviewRegistry  │  │office_engines │  │ build_artifact     │        │
│  │ reject/confirm  │  │revert_office  │  │ save_reviewed      │        │
│  └────────┬────────┘  │    _ops       │  └────────┬───────────┘        │
│           │           └───────────────┘           │                    │
│           ▼                                       ▼                    │
│  ┌─────────────────┐                  ┌────────────────────┐           │
│  │   ReviewSet     │                  │ ChangeTrackingStore│           │
│  │  (纯状态容器)   │                  │   (Office 追踪)    │           │
│  └────────┬────────┘                  └────────────────────┘           │
│           ▼                                                             │
│  ┌─────────────────┐                                                   │
│  │  ReviewEvent    │── append-only → review_events.jsonl               │
│  └────────┬────────┘                                                   │
│           ▼                                                             │
│  ┌──────────────────────────┐     ┌──────────────────┐                 │
│  │FileSystemContentStore    │     │   materializer   │                 │
│  │ blobs/<sha256>           │     │ 原子写入:        │                 │
│  │ put → ContentRef         │────→│ temp→fsync→      │                 │
│  │ get → SHA-256 校验       │     │   os.replace     │                 │
│  └──────────────────────────┘     └──────────────────┘                 │
│           ▲                                                             │
│  ┌────────┴────────┐                                                   │
│  │ reconcile       │── MATCH / DRIFTED / MISSING                       │
│  └─────────────────┘                                                   │
│  ┌─────────────────┐                                                   │
│  │ lifecycle (GC)  │── 引用计数 + 保留窗口 → GcReport                  │
│  └─────────────────┘                                                   │
└──────────────────────────────────────────────────────────────────────────┘
```

**关键设计原则**：
- 事件溯源 + 策略模式：`ReviewSet` 是纯状态容器，不执行 IO；`ReviewRegistry` 负责持久化和物化
- 事件流 append-only（`review_events.jsonl`），永不原地修改
- `ContentStore` 每次 `get` 都重新校验 SHA-256，杜绝静默损坏
- docx 审查**不修改**原文件，而是生成带 tracked-changes 的审查产物
- 策略按格式分派：text/code → `TextChange`，xlsx/pptx → `SpreadsheetChange`，docx → `DocxChange`

## 核心组件

### ReviewRegistry (`registry.py`)

审查子系统的写入侧编排器，是状态变更的唯一入口：

| 方法 | 功能 |
|------|------|
| `get_or_open(conversation_id)` | 获取或创建 ReviewSet（内存缓存 + 事件重放） |
| `register_edit(conversation_id, path, before_content, after_content, ...)` | 记录一次编辑：快照基线 + 记录 head |
| `reject_path(conversation_id, path, force)` | 驳回：恢复到基线（reconcile 冲突时需 force） |
| `confirm_path(conversation_id, path)` | 确认：标记为 accepted |
| `set_decision(conversation_id, path, decision)` | 设置任意决策（proposed/accepted/rejected） |

- 线程安全：`threading.RLock` 保护所有公共方法
- 内存缓存：`_cache: dict[str, ReviewSet]` 按 conversation_id 缓存
- 全局注册表：`get_review_registry(workspace_path)` 工厂函数（按 workspace_path 单例）

### ReviewSet (`review_set.py`)

聚合根，纯状态容器，不执行 IO：

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | `str` | 格式 `rs:{conversation_id}` |
| `conversation_id` | `str` | 关联的会话 ID |
| `baseline` | `dict[str, str \| None]` | path → content_ref（None 表示新文件） |
| `head` | `dict[str, str]` | path → content_ref（最新版本） |
| `head_sha` | `dict[str, str]` | path → sha256（当前 head） |
| `decision` | `dict[str, ReviewDecision]` | path → proposed/accepted/rejected |
| `status` | `str` | open / sealed |

纯状态变更方法（无 IO）：
- `snapshot_baseline(path, content_ref)` — 仅首次快照
- `record_revision(path, content_ref, sha256)` — 记录新版本
- `set_decision(path, decision)` — 设置决策

事件溯源重建：`ReviewSet.rebuild(review_set_id, conversation_id, events)`

### ReviewEvent (`events.py`)

事件溯源记录：

| kind | payload 关键字段 | 说明 |
|------|-----------------|------|
| `open` | — | ReviewSet 创建 |
| `baseline` | path, content_ref | 基线快照（content_ref=None → 新文件） |
| `revision` | path, content_ref, sha256, session_id, tool_call_id, is_binary | 新版本记录 |
| `decision` | path, decision | 用户决策 |
| `materialized` | path, action, sha256 | 物化审计（文件已写回磁盘） |

`ReviewDecision = Literal["proposed", "accepted", "rejected"]`

序列化：`to_line()` / `from_line()` — JSON Lines 格式，每行一个事件

### FileSystemContentStore (`content_store.py`)

Content-addressable blob 存储，完整性即合约：

| 方法 | 功能 |
|------|------|
| `put(data: bytes) → ContentRef` | 写入 blob，返回 `ContentRef(sha256, ref="blobs/{sha}")` |
| `get(ref: ContentRef) → bytes` | 读取 blob，**每次重新校验 SHA-256**（IntegrityError） |
| `exists(ref: ContentRef) → bool` | 检查 blob 是否存在 |

- 存储路径：`<workspace>/.prismclaw/edits/blobs/<sha256hex>`
- 原子写入：先写 `.tmp.{8hex}` 临时文件，再 `replace` 到最终路径
- 完整性保证：`get()` 每次重新计算 SHA-256，bit-rot / 静默损坏 → `IntegrityError`

### materializer (`materializer.py`)

单一原子写入路径：

- `materialize_file(workspace_path, path, content)` — content=None 时删除文件
- 实现：`tempfile.mkstemp` → `os.fdopen(fd, "wb")` → `f.flush()` → `os.fsync()` → `os.replace()`
- 保证：磁盘文件和记录决策只在事件追加失败时可能不一致（基线回滚幂等，重试即可恢复）

### reconcile (`reconcile.py`)

一致性检测状态机：

| 状态 | 条件 | 含义 |
|------|------|------|
| `MATCH` | current_sha == expected_sha | 安全物化基线 |
| `DRIFTED` | current_sha != expected_sha | out-of-band 修改，拒绝盲写（需 force） |
| `MISSING` | 文件不存在 | 按新文件/删除语义处理 |

`ReconcileState` 枚举：MATCH / DRIFTED / MISSING

### 生命周期 GC (`lifecycle.py`)

Blob 垃圾回收，基于引用计数 + 保留窗口：

| 函数 | 功能 |
|------|------|
| `collect_referenced_blobs(edits_dir)` | 从三个 ledger 收集所有被引用的 blob SHA |
| `gc_blobs(edits_dir, dry_run, older_than_days)` | 删除未被引用且超过保留期的 blob |

三个引用来源（联合构成完整活跃集）：
1. `review_events.jsonl` — content_ref / before_ref / after_ref
2. `log.jsonl` — before_ref / after_ref（office edit log）
3. `docx_review_artifacts.jsonl` — before_ref / review_docx_ref

安全规则：被引用的 blob 无条件保留；新于保留窗口的 blob 保留；仅旧且无引用的 blob 被删除

`GcReport` 数据类：workspace、referenced_count、total_blobs、kept_referenced、kept_young、planned_removals、removed、freed_bytes、dry_run

### dispatch (`changes/dispatch.py`)

单一路由点，按文件格式分派审查策略：

```python
def dispatch(fmt, *, registry=None) -> ReviewableChange:
    if fmt in ("xlsx", "pptx"): return SpreadsheetChange(fmt)
    if fmt == "docx":           return DocxChange()
    # default: text/code        return TextChange(registry)
```

### ReviewableChange Protocol (`changes/protocol.py`)

统一审查合约：

```python
class ReviewableChange(Protocol):
    kind: str
    def reject(self, intent: ReviewIntent) -> dict[str, Any]: ...
    def accept(self, intent: ReviewIntent) -> dict[str, Any]: ...
```

### ReviewIntent (`changes/intent.py`)

审查意图数据载体，传递给策略的 `reject`/`accept` 方法：

| 字段 | 说明 |
|------|------|
| `workspace_path` | 工作区路径 |
| `path` | 文件相对路径 |
| `conversation_id` | 会话 ID |
| `current_bytes` | 当前文件字节（office 场景） |
| `rejected` / `accepted` | officeDetail 子集 |
| `force` | 是否强制执行（忽略 drift） |
| `before_ref` / `before_sha` | docx 原始备份 |
| `review_docx_ref` | docx 审查产物引用 |
| `operations` | office 操作列表 |
| `author` | docx 作者标记 |

### TextChange (`changes/text_change.py`)

文本/代码文件审查策略：

- `reject(intent)` → 委托 `ReviewRegistry.reject_path()`
- `accept(intent)` → 委托 `ReviewRegistry.confirm_path()`
- 整文件级回滚：驳回时恢复到 `baseline` 的整个文件

### SpreadsheetChange (`changes/spreadsheet_change.py`)

xlsx / pptx 审查策略：

- `reject(intent)` → 调用 `revert_office_ops()` 逐条反转被拒变更
- `accept(intent)` → 保持现状（文件已是应用状态）
- 精细回滚：只反转 rejected 子集，保留后续轮次的编辑
- 冲突检测：`OfficeRevertConflict`（被拒位置被后续编辑修改）

### DocxChange (`changes/docx_change.py`)

docx 原生审查流程：

- `reject(intent)` → 抛 `OfficeEditError`（docx 驳回不是文件变更，丢弃审查产物即可）
- `accept(intent)` → 抛 `OfficeEditError`（需通过 `save_reviewed()` 显式保存）
- `build_artifact()` → 生成带 tracked-changes 的审查 docx 产物
- `save_reviewed()` → 将用户确认的 docx 内容写回工作区文件

### office_engines (`changes/office_engines.py`)

结构化 Office 编辑/回滚引擎（纯格式逻辑，无 workspace IO）：

| 函数 | 功能 |
|------|------|
| `apply_office_ops(base_bytes, fmt, operations)` | 应用操作列表到文件 |
| `revert_office_ops(current_bytes, fmt, rejected)` | 反转被拒操作 |
| `build_office_detail(base_bytes, fmt, operations)` | 构建审查 UI 的 officeDetail |
| `read_office_text(raw_bytes, fmt)` | 渲染为带 ref 锚点的纯文本 |
| `fmt_from_path(path)` | 路径 → 格式映射（.docx/.xlsx/.pptx） |
| `structured_office_fmt_from_path(path)` | 路径 → 结构化格式映射（.xlsx/.pptx） |

xlsx 操作：`set_cell`（ref: `s#N!r#Rc#C`）
pptx 操作：`set_text_frame` / `replace_run` / `replace_paragraph` / `delete_text_frame`（ref: `slide#N/tf#N`）
重叠安全：`_overlap_decision()` 检测被拒位置是否被后续编辑修改

## 关键数据流

### 文本编辑注册流

```
Tool 写入文件
  → ReviewRegistry.register_edit(conversation_id, path, before, after, session_id, tool_call_id)
    → content.put(before) → ContentRef (baseline, 仅首次)
    → rs.snapshot_baseline(path, base_ref)
    → _append(ReviewEvent kind="baseline")
    → content.put(after) → ContentRef (head)
    → rs.record_revision(path, after_ref, sha256)
    → _append(ReviewEvent kind="revision")
```

### 文本驳回流

```
用户点击 Reject
  → dispatch("text") → TextChange.reject(intent)
    → ReviewRegistry.reject_path(conversation_id, path, force)
      → rs.head_sha_of(path) → expected
      → _current_sha(path) → current
      → reconcile(current, expected)
        → DRIFTED + !force → raise ReconcileConflict
        → MATCH/MISSING → 继续
      → rs.baseline_ref(path)
        → None: materialize_file(workspace, path, None)  # 删除新文件
        → 有值: content.get(ContentRef) → bytes
               materialize_file(workspace, path, data)   # 原子恢复
      → rs.set_decision(path, "rejected")
      → _append(ReviewEvent kind="decision")
      → _append(ReviewEvent kind="materialized")
```

### xlsx 驳回流

```
用户点击 Reject (选择部分变更)
  → dispatch("xlsx") → SpreadsheetChange("xlsx").reject(intent)
    → full.read_bytes() → current
    → revert_office_ops(current, "xlsx", intent.rejected)
      → openpyxl.load_workbook(current)
      → reversed(rejected): _overlap_decision() 检测冲突
        → 安全: cell.value = d.get("before")
        → 冲突: raise OfficeRevertConflict
      → wb.save() → new_bytes
    → full.write_bytes(new)
    → tracking_store.record(FileEditRecord)
```

### docx 审查流

```
1. 生成审查产物:
  → DocxChange.build_artifact(workspace_path, session_id, path, before_ref, operations)
    → store.read_blob_bytes(before_ref) → before_bytes
    → build_docx_review_artifact(before_bytes, operations) → result
    → store.save_blob_bytes(result.review_docx_bytes) → review_docx_ref
    → store.record_docx_review_artifact(DocxReviewArtifactRecord)

2. 用户确认保存:
  → DocxChange.save_reviewed(workspace_path, session_id, path, review_docx_ref, ...)
    → store.get_docx_review_artifact() → 验证 artifact 匹配
    → _sha(current_bytes) vs before_sha → 检测 source drift
    → base64.b64decode(content_base64) → reviewed_bytes
    → full.write_bytes(reviewed_bytes)
    → store.record(FileEditRecord)
```

### Blob GC 流

```
gc_blobs(edits_dir, dry_run=True, older_than_days=30)
  → collect_referenced_blobs(edits_dir)
    → _iter_jsonl(review_events.jsonl) → content_ref, before_ref, after_ref
    → _iter_jsonl(log.jsonl) → before_ref, after_ref
    → _iter_jsonl(docx_review_artifacts.jsonl) → before_ref, review_docx_ref
    → 并集 → referenced: set[str]
  → blobs_dir.iterdir()
    → sha in referenced → kept_referenced
    → mtime > cutoff → kept_young
    → 旧且无引用 → planned_removals / removed
  → return GcReport
```

## 对外接口

### ReviewRegistry（审查注册表）

| 方法 | 返回类型 | 说明 |
|------|----------|------|
| `get_or_open(conversation_id)` | `ReviewSet` | 获取或创建 ReviewSet |
| `register_edit(conversation_id, path, before, after, session_id, tool_call_id, is_binary)` | `None` | 注册编辑 |
| `reject_path(conversation_id, path, force)` | `dict` | 驳回（恢复基线） |
| `confirm_path(conversation_id, path)` | `None` | 确认 |
| `set_decision(conversation_id, path, decision)` | `None` | 设置决策 |

### dispatch（策略分派）

| 函数签名 | 返回类型 | 说明 |
|----------|----------|------|
| `dispatch(fmt, *, registry)` | `ReviewableChange` | 按格式返回审查策略 |

### FileSystemContentStore（内容存储）

| 方法 | 返回类型 | 说明 |
|------|----------|------|
| `put(data)` | `ContentRef` | 写入 blob |
| `get(ref)` | `bytes` | 读取 blob（校验 SHA-256） |
| `exists(ref)` | `bool` | 检查是否存在 |

### lifecycle（GC）

| 函数签名 | 返回类型 | 说明 |
|----------|----------|------|
| `gc_blobs(edits_dir, *, dry_run, older_than_days)` | `GcReport` | 执行 blob GC |
| `collect_referenced_blobs(edits_dir)` | `set[str]` | 收集被引用 blob |

### office_engines（Office 引擎）

| 函数签名 | 返回类型 | 说明 |
|----------|----------|------|
| `apply_office_ops(base_bytes, fmt, operations)` | `bytes` | 应用操作 |
| `revert_office_ops(current_bytes, fmt, rejected)` | `bytes` | 反转操作 |
| `build_office_detail(base_bytes, fmt, operations)` | `list[dict]` | 构建审查详情 |
| `read_office_text(raw_bytes, fmt)` | `str` | 渲染为文本 |

## 依赖关系

- **Schema**：`prismclaw.schema.agent.review.ContentRef`、`ContentStore`
- **Schema**：`prismclaw.schema.agent.review_errors.IntegrityError`、`ReconcileConflict`、`ReviewSetNotFound`、`OfficeEditError`、`OfficeRevertConflict`、`DocxArtifactMismatch`、`DocxSourceDrift`、`OfficeBlobNotFound`
- **Schema**：`prismclaw.schema.agent.curator_types.CuratorReport`、`CycleReport`、`ReviewOutcome`
- **Toolkit**：`prismclaw.engine.toolkit.office.docx_review_native.build_docx_review_artifact`
- **Toolkit**：`prismclaw.engine.toolkit.file.tracking`（FileEditRecord、DocxReviewArtifactRecord、get_tracking_store）
- **Toolkit**：`prismclaw.engine.toolkit.file.workspace.clean_rel_path`
- **外部库**：`openpyxl`（xlsx）、`python-pptx`（pptx）

## 配置项

审查系统的运行时行为主要由代码内常量控制，暂无环境变量配置：

| 参数 | 值 | 说明 |
|------|-----|------|
| `_REVIEW_PROMPT_CHAR_BUDGET` | 60,000 | LLM 审查 prompt 字符预算（实际位于 `persistence/curator/repository.py`，批次级） |
| Blob 存储路径 | `<workspace>/.prismclaw/edits/blobs/` | content-addressable blob 目录 |
| 事件日志路径 | `<workspace>/.prismclaw/edits/review_events.jsonl` | ReviewRegistry 事件流 |
| GC 默认保留期 | 30 天 | `{}