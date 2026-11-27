# 病历解析流水线重构 — 软件设计文档（SDD）

> 对齐 `medical-domain-design.md` §6.3 与 `smart-medical-record-architecture-v2.md` 患者中心模型。

---

## 一、范围与动机

### 1.1 当前架构

上传→解析→分析的链路由三个文件串联：

| 文件 | 行数 | 职责 |
|---|---|---|
| `api/records.py` | 327 | 上传端点 + `BackgroundTasks` 编排 |
| `services/parse_pipeline.py` | 211 | 四阶段解析状态机 |
| `domain/parsing/extract.py` | 389 | 文件→文本抽取（含 OCR） |

调用链路：

```
POST /upload  →  background_tasks.add_task(_run_parse_then_maybe_analysis)
                     ↓
              run_parse_pipeline(store, report_id, llm=, vision=)
                     ↓
              extract_text(data, filename, ocr=ocr_image)
                     ↓
              _ocr_join(images, mime, ocr)   ← 逐张串行
              complete_json(llm, ...)          ← 元数据
              complete_json(llm, ...)          ← 结构化
              match_patient(...)
                     ↓（解析成功+归属患者）
              trigger_analysis_for_patient(...)
```

当前并发模型：每个上传文件各启一个 `BackgroundTasks`，无优先级、无持久化、无重试、无并发上限。分析流水线有 `Semaphore(2)` 限流，解析流水线无任何限流。

### 1.2 问题清单

| 编号 | 问题 | 严重度 | 影响 |
|---|---|---|---|
| P1 | 无并发控制：N 文件上传 = N 路并行解析，LLM/Vision 无 Semaphore 保护 | 高 | 429 / OOM |
| P2 | `_ocr_join()` 逐张串行 OCR，10 页扫描件耗时线性堆叠 | 高 | 单份扫描件 40s+，占总耗时 42% |
| P3 | `BackgroundTasks` 无持久化：进程重启丢任务，用户只能手动 reparse | 中 | 数据丢失感 |
| P4 | `_docx_images(max_images=8)` 截图上限 8 张，`_docx_parse()` 返回全部图片（实测 83 张）——但 83 张静默截断到 8 张会丢失关键检查图片 | 中 | 医疗安全隐患 |
| P5 | 双重截断：`RAW_TEXT_LIMIT=30,000`（extract.py）+ `PROMPT_TEXT_LIMIT=24,000`（parse_pipeline.py），高图文档尾部内容丢弃 | 中 | LLM 缺失关键临床信息 |
| P6 | 无用户公平调度：A 用户批量上传 20 份会占满资源，B 用户排不到 | 中 | 体验差（Phase 1 单用户场景实际不触发） |
| P7 | `PDF_MAX_OCR_PAGES=8` 硬编码常量，不同文档类型/模型能力无法适配 | 低 | 扩展性差 |
| P8 | `complete_json` 两次重试（temp 0 → 0.2），失败返回 None 无后续降级策略 | 低 | 元数据/结构化静默丢失 |
| P9 | 进度写入无细粒度 OCR 页级进度：20→40 之间黑盒，前端无法展示「第 3/10 页识别中」 | 低 | 用户焦虑 |
| P10 | `_ocr_join` 中间页失败立即中止并丢弃已识别内容 | 低 | 不必要的信息丢失 |

### 1.3 设计决策与取舍

**决策 1：Phase 1a = Semaphore only，Phase 1b = Queue（YAGNI 分层）**

P1（无并发控制）的解法是加 Semaphore，不是加队列。`analysis_pipeline.py` 已有 `Semaphore(2)` 解决同类问题，只用了 3 行代码。parse_pipeline 同理。

队列解决的 P3（持久化）和 P6（公平调度）在当前单医生工作台场景下：
- P3：进程重启极少发生，且 reparse 端点已可手动恢复
- P6：单用户场景不会出现抢占

YAGNI 判据：「不做也不痛」→ 队列化推迟到实测证明需要时再上。

**决策 2：统一 LLM Semaphore**

parse_pipeline 和 analysis_pipeline 共享同一个 LLM 端点，但各有各的信号量（parse 新增的 `llm_sem` + analysis 已有的 `_SEMAPHORE`），总并发可达 4 ≠ 声称的 2。

方案：改为一把全局 `medical_llm_sem = Semaphore(2)`，两条流水线共享。预算分配由信号量自然调度（parse 阶段占用时 analysis 等待，反之亦然），无需额外仲裁。

**决策 3：图片截断→并行 OCR 全量**

医疗场景静默截断 75/83 张图片是危险的——被丢弃的图片可能包含病理切片、免疫组化、PET-CT 等关键检查。

OCR 并行化的真正价值不是提速，而是**让全量 OCR 变得可行**。并行 83 张图（Semaphore(3)）约 140s，串行 83 张约 415s——415s 不可接受所以才有截断，140s 可接受所以截断不再需要。

**决策 4：assign 端点不走 ParseQueue**

assign（L187）只触发 `trigger_analysis_for_patient`，不需要 parse。如果走 ParseQueue 的 `_process`（硬编码 parse→analysis 全链路），要么对已 PARSED 报告重复解析，要么 ParseTask 需 mode 字段分支——增加复杂度但无收益。

assign 端点保持现有 `background_tasks.add_task(trigger_analysis)` 不变，仅把 trigger 内部的 LLM 调用纳入全局信号量保护。

---

## 二、目标架构

### Phase 1a（本 SDD 交付范围）✅ 已落地（2026-09-08）

```
  POST /upload  ─→  background_tasks.add_task(_run_parse_then_maybe_analysis)
  POST /reparse ─→  background_tasks.add_task(_run_parse_then_maybe_analysis)  ← 保持不变
  POST /assign  ─→  background_tasks.add_task(trigger_analysis)                ← 保持不变
                          │
                          ▼
              run_parse_pipeline(store, report_id, llm, vision,
                                 ocr_sem, llm_sem)          ← 新增两把 Semaphore
                          │
              ┌───────────┼──────────────┐
              ▼           ▼              ▼
         OCR 并行     元数据/结构化    match_patient
    Semaphore(3)    全局 LLM Sem(2)       │
    asyncio.gather                    归属患者
    页级进度回调                          │
              │                          ▼
              ▼              trigger_analysis_for_patient()
              └─────────── 共享全局 LLM Semaphore(2) ──┘
```

核心变化（4 项）：

1. **parse_pipeline 加双 Semaphore**：`ocr_sem=Semaphore(3)` + 全局 `llm_sem=Semaphore(2)`
2. **OCR 并行化**：`_ocr_join` → `_ocr_join_parallel`，`asyncio.gather` + Semaphore(3)
3. **图片截断→全量 OCR**：取消 `max_images` 截断，并行 OCR 全部图片
4. **页级进度**：OCR 回调注入进度更新

### Phase 1b（后续，触发条件：实测出现 P3/P6）

```
  POST /upload  ─→  ParseQueue.enqueue(ParseTask, priority=NEW_UPLOAD)
  POST /reparse ─→  ParseQueue.enqueue(ParseTask, priority=INTERACTIVE_RETRY)
  POST /assign  ─→  background_tasks.add_task(trigger_analysis)  ← 不变
                          │
                    ParseQueue + Worker Pool
                    优先级调度 / 用户公平 / 持久化就绪
```

---

## 三、详细设计（Phase 1a）

### 3.1 全局 LLM Semaphore

```python
# integration/llm_access.py — 新增

from asyncio import Semaphore

# 全局 LLM 信号量：parse_pipeline + analysis_pipeline 共享
# 两阶段（元数据+结构化）串行占用 1 个槽，不叠加
MEDICAL_LLM_SEMAPHORE = Semaphore(2)
```

analysis_pipeline 现有的 `_SEMAPHORE = Semaphore(2)` 替换为引用此全局实例：

```python
# services/analysis_pipeline.py — 改动

from ..integration.llm_access import MEDICAL_LLM_SEMAPHORE

# 删除: _SEMAPHORE = Semaphore(2)
# 改为:
_SEMAPHORE = MEDICAL_LLM_SEMAPHORE
```

**不变量**：任意时刻 parse_pipeline + analysis_pipeline 的 LLM 并发调用总数 ≤ 2。

### 3.2 OCR 并行化

#### 3.2.1 `_ocr_join_parallel`

```python
# domain/parsing/extract.py

async def _ocr_join_parallel(
    images: list[bytes],
    mime: str,
    ocr: OcrFn,
    *,
    ocr_sem: asyncio.Semaphore,
    on_page_done: Callable[[int, int], Awaitable[None]] | None = None,
) -> tuple[str, str]:
    """并行 OCR 后拼接。

    与 _ocr_join 的行为差异：
    - 并行执行，总耗时 ≈ max(单页) 而非 sum(全部)
    - 任一页失败不中止其余页（P10 修复）
    - 页级进度回调（P9 修复）
    - ocr_sem 必须由调用方传入，保证同一信号量在所有协程间共享
    """
    total = len(images)
    results: list[tuple[int, str]] = []   # (page_index, text)
    errors: list[tuple[int, str]] = []    # (page_index, error)

    async def _ocr_one(idx: int, img: bytes) -> None:
        async with ocr_sem:                          # 共享信号量，在外层创建一次
            try:
                page_text = await ocr(img, mime)
                if page_text:
                    results.append((idx, page_text.strip()))
            except Exception as exc:  # noqa: BLE001
                errors.append((idx, f"第 {idx + 1} 页 OCR 失败: {exc}"))
            finally:
                if on_page_done:
                    await on_page_done(idx + 1, total)

    await asyncio.gather(*[_ocr_one(i, img) for i, img in enumerate(images)])

    # 按页码排序拼接（保持原始顺序）
    results.sort(key=lambda r: r[0])
    multipage = total > 1
    parts: list[str] = []
    for idx, text in results:
        prefix = f"— 第 {idx + 1} 页 —\n" if multipage else ""
        parts.append(prefix + text)

    joined = _collapse("\n".join(parts))
    if not joined:
        if errors:
            return "", errors[0][1]
        return "", "OCR 未识别出文字"

    if errors:
        logger.warning("OCR 部分页失败: %s", "; ".join(e for _, e in errors))

    return joined[:RAW_TEXT_LIMIT], ""
```

#### 3.2.2 Semaphore 正确性

信号量在 `_ocr_join_parallel` 入口处**由调用方传入**（而非函数内部创建），保证所有 `_ocr_one` 协程共享同一个 `asyncio.Semaphore` 实例。这修复了初版中 `ocr_sem or asyncio.Semaphore(3)` 在每次调用时创建新实例导致无限流的 bug。

#### 3.2.3 效率预期

| 场景 | 串行耗时 | 并行耗时（Semaphore=3） | 提升比 |
|---|---|---|---|
| 5 页扫描件 | ~25s | ~9s | 2.8× |
| 10 页扫描件 | ~50s | ~18s | 2.8× |
| 83 图 docx | ~415s | ~140s | 3.0× |
| 3 张图 docx | ~15s | ~5s | 3.0× |

单页 OCR 约 5s（Vision 模型 RTT），Semaphore(3) 保证同时 ≤3 路 OCR 调用。

### 3.3 图片截断→全量 OCR

**现状问题**：`_docx_parse()` 返回全部图片（实测 83 张），`_docx_images()` 截断到 `PDF_MAX_OCR_PAGES=8`。两者不一致，且医疗场景静默丢弃图片有安全隐患。

**方案**：OCR 并行化后全量 OCR 可行，不再截断图片。

| 路径 | 改动 |
|---|---|
| `_docx_parse()` | 不加 `max_images` 截断——并行 OCR 让 83 张图可在 140s 内处理 |
| `_docx_images()` | 保留 `max_images` 参数作为**可配置上限**（默认值可调），但不是截断丢弃——超出部分仍 OCR，仅当总 OCR 文本超 `RAW_TEXT_LIMIT` 时按尾部截断（这是已有行为） |
| `PDF_MAX_OCR_PAGES` | 保留为 PDF 渲染上限（防上百页扫描件撑爆内存），但值可配置 |
| `extract_text()` | 传递 `max_ocr_pages` 时语义变更：PDF 渲染上限（内存保护），而非 OCR 截断上限 |

**安全性保证**：不再静默丢弃任何图片的 OCR 结果。如果 OCR 总文本超过 `RAW_TEXT_LIMIT`，截断发生在文本层（可感知、可记录），而非图片层（不可感知）。

### 3.4 parse_pipeline 接收 Semaphore

```python
# services/parse_pipeline.py — 签名变更

async def run_parse_pipeline(
    store: MedicalStore,
    report_id: str,
    *,
    llm: Any | None = None,
    vision: Any | None = None,
    ocr_sem: asyncio.Semaphore | None = None,   # 新增
    llm_sem: asyncio.Semaphore | None = None,    # 新增
) -> None:
```

内部变更点：

1. `_run()` 创建 OCR 闭包时，将 `ocr_sem` 传递给 `extract_text()`
2. `complete_json` 调用包裹在 `llm_sem` 保护下
3. 默认 None 时行为不变（向后兼容：单测 / 旧调用方式不受影响）

```python
# _run() 内部改动示意

# ── OCR Semaphore 创建 ──
_ocr_sem = ocr_sem or asyncio.Semaphore(3)

# ── LLM guarded 调用 ──
async def _guarded_json(*args, **kwargs):
    if llm_sem:
        async with llm_sem:
            return await complete_json(*args, **kwargs)
    return await complete_json(*args, **kwargs)

# 阶段 1：文本抽取
raw_text, err = await extract_mod.extract_text(
    data, filename, ocr=ocr, ocr_sem=_ocr_sem,
    on_page_done=_on_page_done,   # 见 §3.6
)

# 阶段 2/3：结构化提取
meta_raw = await _guarded_json(llm, system=METADATA_SYSTEM, ...)
struct_raw = await _guarded_json(llm, system=STRUCTURE_SYSTEM, ...)
```

### 3.5 截断策略收敛

**现状**：`RAW_TEXT_LIMIT=30,000`（extract.py 末尾截断）→ `PROMPT_TEXT_LIMIT=24,000`（parse_pipeline.py 送 LLM 前截断），两次截断叠加。

**方案**：

- `RAW_TEXT_LIMIT` 保留作为**存储上限**（raw_text 字段不无限膨胀），仅防溢出
- `PROMPT_TEXT_LIMIT` 作为**唯一语义截断点**，送 LLM 前截断
- `_ocr_join_parallel` 返回时截断到 `RAW_TEXT_LIMIT`（已有行为）
- `parse_pipeline.py` 只做 `PROMPT_TEXT_LIMIT` 截断（已有行为）

不变量：LLM 看到的文本 ≤ `PROMPT_TEXT_LIMIT`，存储的 raw_text ≤ `RAW_TEXT_LIMIT`。

全量 OCR 后 `RAW_TEXT_LIMIT` 可能不够用（83 张图 OCR 文本可达 50,000+ 字符），此时截断发生在文本层而非图片层。后续可根据实测数据考虑：
- 方案 A：调高 `RAW_TEXT_LIMIT`（存储成本增加，但 DB text 字段无硬上限）
- 方案 B：OCR 结果只存摘要 + 全文放对象存储（Phase 1b 考虑）

### 3.6 进度反馈增强

**现状**：进度只有 5 个粗粒度节点（0/20/40/70/90/100）。

**方案**：OCR 阶段注入页级进度回调：

```python
# parse_pipeline.py — _run() 内部

# 阶段 1：文本抽取（带 OCR 页级进度）
async def _on_page_done(page: int, total: int) -> None:
    # OCR 进度映射到 20-40 区间
    ocr_progress = 20 + int(20 * page / total)
    await _advance(store, report_id, ReportStatus.OCR.value, ocr_progress)

raw_text, err = await extract_mod.extract_text(
    data, filename, ocr=ocr, ocr_sem=_ocr_sem,
    on_page_done=_on_page_done,
)
```

进度区间重映射：

| 阶段 | 进度区间 | 说明 |
|---|---|---|
| 排队中 | 0 | 已入库，等待 Worker 消费 |
| OCR 识别 | 20–40 | 页级进度：`20 + 20 × (done/total)` |
| 元数据提取 | 40 | 单次 LLM 调用，无细分 |
| 结构化提取 | 40–70 | 单次 LLM 调用，无细分 |
| 患者归属 | 90 | 纯匹配，瞬时 |
| 已解析 | 100 | 完成 |

### 3.7 records.py 改造

Phase 1a 保持 `BackgroundTasks` 编排不变，仅在 `_run_parse_then_maybe_analysis` 内部传入 Semaphore：

```python
# api/records.py — _run_parse_then_maybe_analysis 改造

from ..integration.llm_access import MEDICAL_LLM_SEMAPHORE

async def _run_parse_then_maybe_analysis(
    store, brief_store, kb_store, report_id,
    *, user_id="system", llm=None, vision=None,
) -> None:
    # 1. 解析流水线——传入 Semaphore
    await run_parse_pipeline(
        store, report_id, llm=llm, vision=vision,
        ocr_sem=asyncio.Semaphore(3),          # OCR 并发上限
        llm_sem=MEDICAL_LLM_SEMAPHORE,         # 全局 LLM 并发上限
    )

    # 2. 检查是否触发分析（保持不变）
    report = await store.get_report(report_id)
    if report is None:
        return
    if str(report.get("status") or "") != ReportStatus.PARSED.value:
        return
    patient_id = str(report.get("patient_id") or "")
    if not patient_id:
        return

    # 3. 分析——内部已通过 _SEMAPHORE = MEDICAL_LLM_SEMAPHORE 受全局保护
    try:
        await trigger_analysis_for_patient(
            store, brief_store, kb_store, patient_id,
            user_id=user_id, llm=llm, vision=vision,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "解析后自动分析失败 report_id=%s patient_id=%s", report_id, patient_id,
        )
```

**三处 `add_task` 调用点不变**——upload（L275）、assign（L187）、reparse（L317）仍用 `background_tasks.add_task()`，因为 Phase 1a 不引入队列。

assign 端点（L187）只触发 `trigger_analysis_for_patient`，后者内部已通过 `_SEMAPHORE = MEDICAL_LLM_SEMAPHORE` 受全局 LLM 信号量保护，无需额外改动。

### 3.8 `PDF_MAX_OCR_PAGES` 可配置化

```python
# extract.py — extract_text 签名扩展

async def extract_text(
    data: bytes,
    filename: str,
    *,
    ocr: OcrFn | None = None,
    max_ocr_pages: int = PDF_MAX_OCR_PAGES,  # 已有，语义变为 PDF 渲染上限
    ocr_sem: asyncio.Semaphore | None = None,       # 新增
    on_page_done: Callable[[int, int], Awaitable[None]] | None = None,  # 新增
) -> tuple[str, str]:
```

`max_ocr_pages` 语义变更：
- **PDF**：渲染上限（防止上百页扫描件撑爆内存，渲染是 CPU/内存密集操作）
- **docx**：不再截断图片（OCR 是 IO 密集非 CPU 密集，并行化后全量可行）

---

## 四、接口契约变更

### 4.1 函数签名变更

| 函数 | 变更 | 向后兼容 |
|---|---|---|
| `run_parse_pipeline(store, report_id, *, llm, vision)` | 增加 `ocr_sem`, `llm_sem` 可选参数 | ✅ 默认 None，旧行为不变 |
| `extract_text(data, filename, *, ocr, max_ocr_pages)` | 增加 `ocr_sem`, `on_page_done` 可选参数 | ✅ 默认 None，旧行为不变 |
| `_ocr_join(images, mime, ocr)` | 保留，不删（测试可能依赖） | — |
| 新增 `_ocr_join_parallel(images, mime, ocr, *, ocr_sem, on_page_done)` | — | 新增 |
| `MEDICAL_LLM_SEMAPHORE` | 新增全局常量 | — |
| `analysis_pipeline._SEMAPHORE` | 从本地 `Semaphore(2)` 改为引用 `MEDICAL_LLM_SEMAPHORE` | 行为不变 |

### 4.2 API 端点变更

Phase 1a **无 API 变更**。三处端点保持 `background_tasks.add_task()` 不变。

### 4.3 行为变更

| 行为 | 变更 | 风险 |
|---|---|---|
| OCR 执行方式 | `_ocr_join` 串行 → `_ocr_join_parallel` 并行 | OCR 结果拼接顺序不变（按页码排序），但中间页失败不再中止（P10 修复：行为变好） |
| docx 图片处理 | 不截断，全量 OCR | 内存：83 张图 OCR 并行 Semaphore(3) 控制并发，不会 OOM；耗时：~140s 可接受 |
| LLM 并发 | 无上限 → 全局 Semaphore(2) | 解析/分析并发降低，单份处理延迟可能微增，但总量吞吐更稳定 |

---

## 五、文件变更清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `integration/llm_access.py` | **修改** | 新增 `MEDICAL_LLM_SEMAPHORE = Semaphore(2)` 全局常量 |
| `services/parse_pipeline.py` | **修改** | 增加 `ocr_sem` / `llm_sem` 参数；OCR 进度回调；LLM 调用加 Semaphore |
| `services/analysis_pipeline.py` | **修改** | `_SEMAPHORE` 改为引用 `MEDICAL_LLM_SEMAPHORE`（1 行改动） |
| `domain/parsing/extract.py` | **修改** | 新增 `_ocr_join_parallel`；`extract_text` 增加 `ocr_sem` / `on_page_done`；调用点切换 |
| `api/records.py` | **修改** | `_run_parse_then_maybe_analysis` 传入 Semaphore（3 行改动） |
| `models/enums.py` | **不改** | ReportStatus.QUEUED 已存在 |

---

## 六、迁移路径

### Phase 1a：Semaphore + OCR 并行（本 SDD 交付范围）✅ 已落地（2026-09-08）

1. `llm_access.py` 新增 `MEDICAL_LLM_SEMAPHORE`
2. `analysis_pipeline.py` 引用全局 Semaphore
3. `extract.py` 新增 `_ocr_join_parallel`，`extract_text` 参数扩展
4. `parse_pipeline.py` 增加 `ocr_sem` / `llm_sem`，OCR 进度回调
5. `records.py` 传入 Semaphore
6. 全量回归测试

### Phase 1b：队列化（触发条件：实测出现 P3 或 P6）

触发前不需要做的：
- ParseQueue / ParseTask / Worker Pool
- queue_access.py
- records.py 的 add_task → enqueue 替换
- 优先级调度逻辑

触发后：
- 新增 `services/parse_queue.py` + `integration/queue_access.py`
- `records.py` 三处 `add_task` → `queue.enqueue`
- app lifespan 中启动/停止 ParseQueue
- `ParseQueue.stop()` 采用 graceful drain：设 `_running=False` → Worker 排空队列 → 超时后 cancel

### Phase 2：持久化就绪（后续）

- 将 ParseTask 序列化到数据库（`pc_parse_tasks` 表），进程重启后恢复
- 前端 WebSocket 推送队列位置 / 预估等待时间

### Phase 3：分布式就绪（远期）

- 替换 `asyncio.PriorityQueue` 为 Redis Stream / Celery
- 多 Worker 进程 / 多节点水平扩展

---

## 七、验收标准

| 项 | 标准 |
|---|---|
| 并发安全 | 同时上传 20 份报告，OCR 并发 ≤3、LLM 并发 ≤2（parse+analysis 总计），无 429 |
| OCR 并行 | 10 页扫描件 OCR 耗时 < 串行耗时的 40% |
| 向后兼容 | `run_parse_pipeline(store, rid, llm=l, vision=v)` 不传 sem 仍可运行 |
| 全量 OCR | 83 图 docx 不截断图片，全部 OCR 后拼接 |
| 截断可见 | OCR 总文本超 `RAW_TEXT_LIMIT` 时截断发生在文本层（非图片层），前端可感知 |
| 进度细粒度 | OCR 阶段前端可观察到页级进度变化（20→22→24→...→40） |
| 容错 | OCR 个别页失败不中止整份报告，已识别页保留 |
| 全局 LLM | parse + analysis 任意时刻 LLM 并发调用总数 ≤ 2 |
