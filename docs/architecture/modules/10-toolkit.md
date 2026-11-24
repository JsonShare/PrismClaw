# 工具集

## 职责概述

工具集（Toolkit）模块为 Agent 提供「手」——一组可被 LLM 调用的工具，涵盖文件读写、Bash 命令执行、目录列表、文件搜索、任务管理、用户交互等能力。模块同时提供文件观察器（FileObserver）、文件变更队列（mutation queue）和 OCR 缓存等基础设施，确保并发安全和一致性。

## 架构总览

**[查看交互式架构图](../diagrams/10-toolkit.html)**

```
┌──────────────────────────────────────────────────────────────────┐
│                        Toolkit 双层架构                           │
│                                                                  │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                 Builtin 内置工具层                           │ │
│  │                                                             │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐      │ │
│  │  │   Bash   │ │  BashRead│ │   Read   │ │  Write   │      │ │
│  │  │ (write)  │ │  (read)  │ │  (read)  │ │ (write)  │      │ │
│  │  └────┬─────┘ └──────────┘ └────┬─────┘ └────┬─────┘      │ │
│  │       │                        │              │             │ │
│  │  ┌────┴─────┐ ┌──────────┐     │              │             │ │
│  │  │   Edit   │ │    LS    │     │              │             │ │
│  │  │ (write)  │ │  (read)  │     │              │             │ │
│  │  └────┬─────┘ └──────────┘     │              │             │ │
│  │       │                        │              │             │ │
│  │  ┌────┴────────────────────────┴──────────────┘             │ │
│  │  │           read_epoch 乐观并发控制                         │ │
│  │  └──────────────────────┬──────────────────────┘            │ │
│  │                         │                                   │ │
│  │  ┌──────────────┐ ┌────────────────────┐                    │ │
│  │  │ CancelTask   │ │  AskUserQuestion   │                    │ │
│  │  │ (skill-gated)│ │  (interactive)     │                    │ │
│  │  └──────────────┘ └────────────────────┘                    │ │
│  └─────────────────────────┼───────────────────────────────────┘ │
│                            │ 读写依赖                            │
│                            ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │                File 文件基础设施层                           │ │
│  │                                                             │ │
│  │  ┌────────────────────┐  ┌────────────────────────────┐    │ │
│  │  │   FileObserver     │  │  FileMutationQueue         │    │ │
│  │  │ (SHA-256 版本跟踪) │  │  (每文件 asyncio.Lock      │    │ │
│  │  │                    │  │   并发写入串行化)           │    │ │
│  │  └─────────┬──────────┘  └────────────────────────────┘    │ │
│  │            │ read_epoch                                     │ │
│  │  ┌─────────┴──────────┐  ┌────────────────────────────┐    │ │
│  │  │     GlobTool       │  │      GrepTool              │    │ │
│  │  │  (文件模式搜索)     │  │   (内容搜索)               │    │ │
│  │  └────────────────────┘  └────────────────────────────┘    │ │
│  │                                                             │ │
│  │  ┌────────────────────┐                                    │ │
│  │  │    OCR Cache       │                                    │ │
│  │  │ (stat指纹缓存)     │                                    │ │
│  │  └────────────────────┘                                    │ │
│  └─────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

## 核心组件

### BashTool

```python
# builtin/bash.py
class BashTool:
    name = "bash"
    tool_class = "write"
    # 持久 PTY 会话
    # blocking_budget_ms: 15 秒上限
    # hard_timeout: 强制终止
    # 软超时时 detach
    # 执行: runtime.bash()
```

### ReadTool

```python
# builtin/file_tools.py
class ReadTool:
    name = "read"
    tool_class = "read"
    # 分页读取: DEFAULT_PAGE_LINES = 2000
    # MAX_LINE_LENGTH = 2000
    # read_epoch (SHA-256) 乐观并发控制
    # 同工具内分支（不再是独立工具）：图片 → read_image 视觉分支；
    #   文档（.pdf/.docx/.xls/.md/.rst/.rtf/.odt/.ods/.odp/.epub/.ofd/.html/
    #   .htm/.mhtml/.mht/.csv/.tsv）→ read_office 按页分支；
    #   结构化 Office（.xlsx/.pptx）→ 保留结构锚点的行式读取 + 内嵌图片清单

### 文档按页读取（document_reader）

```python
# toolkit/office/document_reader.py — 解析层唯一真源
DOC_FORMATS                       # 扩展名 → fmt，全仓唯一表
PAGELESS_FORMATS                  # 无页概念格式集合（恒第 1 页；
                                  #   pdf/odp/epub/ofd 有真实页号，不在此列）
Block(kind, page, index, text, rows, source_path, mime_type, is_ocr)
open_doc(raw, fmt, *, workspace_path, source_rel_path)   # pdf: pdfium；docx: python-docx；
                                                          # markdown: str；其余: DocHandle
parse_page(doc, page)             # 只取文本层，不外发；DocHandle 直接返回预解析块
render_page_images(doc, pages)    # 锁内把页渲染为 JPEG，供锁外 OCR（仅 PDF）
render_page_image(doc, page)      # 单页渲染（单页 vision 读取用）
build_ocr_page_blocks(...)        # OCR 文本 → 块（短文本附页面图）
render_blocks(blocks, hash8, tier, start, char_budget) -> (text, Cursor | None)
render_blocks_by_page(...)        # 批量读取：按页装填预算
parse_locator("doc:{h}/p{n}/b{n}@{tier}[/c{n}]") -> Cursor   # 1-based 自校验
cache_dir / ensure_cache / load_page / save_page             # 会话级单页 JSON 缓存
search_blocks(...)                # grep 的文档分支，只扫已解析页
```

- 定位符 `doc:{hash8}/p3/b7@text`：`hash8` 为内容哈希前 8 位，块号以 text 档为唯一权威。
- 缓存落 `<root>/.work/cache/doc/<sha1前16>/`，单页 JSON + `meta.json`（身份字段 + 页摘要）；旧代码回滚后静默忽略，由 `workspace_gc` 回收。
- pdfium 原生库非线程安全（并发 `get_textpage`/`render` 会 SIGABRT），全部调用经模块级锁串行化；页文件与其摘要成对落盘，共用一把缓存锁。
- 写入侧拒绝模型改写缓存：`WorkspaceResolver.resolve_within(for_write=True)` 按物理路径拒绝任何 `.work/` 目标。

### WriteTool

```python
# builtin/file_tools.py
class WriteTool:
    name = "write"
    tool_class = "write"
    # 二进制扩展名阻断（_blocked_binary_extension）
    # review registry 集成
    # read_epoch 跟踪
```

### EditTool

```python
# builtin/file_tools.py
class EditTool:
    name = "edit"
    tool_class = "write"
    # old_string → new_string 替换
    # read_epoch 验证（乐观并发）
    # Python 语法检查
    # 模糊匹配提示
    # review registry 集成
```

### ListDirectoryTool

```python
# builtin/file_tools.py
class ListDirectoryTool:
    name = "ls"
    tool_class = "read"
    # 执行: runtime.fs_list()
```

### SubagentControlTool

```python
# engine/subagent/control_tool.py
class SubagentControlTool:
    name = "subagent_control"
    tool_class = "write"
    skill_gated = False
    # status / list / interrupt / send / resume / close
    # 除 list 外必须同时提供 (agent_id, task_id)
```

### AskUserQuestionTool

```python
# builtin/task_tools.py
class AskUserQuestionTool:
    name = "AskUserQuestion"
    tool_class = "read"
    requires_interactive_user = True
    # 1-4 个问题，每个 2-4 个选项
```

### GlobTool

```python
# file/glob.py
class GlobTool:
    name = "glob"
    tool_class = "read"
    # 执行: runtime.fs_glob()
    # max_results = 100
    # 工作区边界强制
```

### GrepTool

```python
# file/grep.py
class GrepTool:
    name = "grep"
    tool_class = "read"
    # 执行: runtime.fs_grep()
    # max_matches = 250
    # case_sensitive 选项
```

### FileObserver

```python
# file/file_observer.py
class FileObserver:
    # 跟踪 FileVersion(read_epoch=SHA-256, line_count)
    # VersionCheck 枚举: OK / STALE / UNKNOWN
    # sync_to_dict() / sync_from_dict() 序列化
```

每次 `ReadTool` 读取文件后，FileObserver 记录文件的 SHA-256 摘要和行数。后续 `EditTool` / `WriteTool` 写入前检查 `read_epoch`，若不一致则拒绝写入（乐观并发控制）。

### FileMutationQueue

```python
# file/file_mutation_queue.py
def with_file_mutation_queue()   # 每文件 asyncio.Lock 串行化并发写入
def clear_mutation_queues()      # session 结束清理
```

确保同一文件的并发写入操作按序执行，避免竞态条件。

### OCR 缓存

```python
# file/ocr_cache.py
def ocr_cache_path()      # 基于 stat 指纹的缓存路径（.extracted/.ocr_cache/）
def read_ocr_cache()      # 读取缓存
def write_ocr_cache()     # 写入缓存
```

缓存键基于文件的 stat 信息（mtime + size）自动失效，避免重复 OCR 处理。文档解析缓存另有一套（见下），键是文件内容哈希，不依赖 stat。

图片识别入口是 `read_image.recognize_image(path, data, question, workspace_path)`；`DEFAULT_QUESTION` 是唯一可缓存的提问（自定义提问的回答不复用）。

### 辅助常量与函数

```python
# builtin/file_helpers.py
BASH_DEFAULT_TIMEOUT_SECONDS = 120.0
_READ_DEFAULT_PAGE_LINES = 2000
_BASH_READBACK_MAX_CHARS = 200_000

_blocked_binary_extension()    # 阻断 Office/PDF/ODF/EPUB/RTF/MHTML 等二进制格式的文本写入
_binary_write_guidance()       # 提供替代方案提示
build_file_change()            # difflib hunk 计算（UI artifact），大文件截断
```

```python
# builtin/tool_helpers.py
_is_ask_user_question()
_extract_file_paths_from_tool_calls()
_clarify_timeout()
_build_budget_warning_message()
_entry_to_llm_dict()
```

## 关键数据流

### 文件读取 → 编辑流（乐观并发控制）

```
ReadTool.execute()
  → 读取文件内容
  → FileObserver 记录 read_epoch = SHA-256(file_content)
  → 返回内容 + read_epoch

EditTool.execute(old_string, new_string, read_epoch)
  → FileObserver 检查: VersionCheck
    → STALE: 拒绝编辑，返回文件已变更错误
    → OK/UNKNOWN: 继续
  → with_file_mutation_queue(file_path):
    → 执行 old_string → new_string 替换
    → Python 语法检查（.py 文件）
    → 更新 FileObserver read_epoch
  → build_file_change() 计算 diff hunk
  → 写入 ui_artifact
```

### Bash 命令执行流

```
BashTool.execute(command, blocking_budget_ms)
  → _clarify_timeout() 计算 hard_timeout
  → runtime.bash(command)
    → 持久 PTY 会话中执行
    → 软超时: detach（进程继续运行）
    → 硬超时: 强制终止
  → _build_budget_warning_message()（超限时）
```

### 文件搜索流

```
GlobTool.execute(pattern)
  → runtime.fs_glob(pattern)
  → max_results=100 截断
  → 工作区边界校验

GrepTool.execute(pattern, case_sensitive)
  → runtime.fs_grep(pattern)
  → max_matches=250 截断
```

### OCR 缓存流

```
ReadTool 检测到图片 → 走视觉分支（read_image）
  → ocr_cache_path(file_path)  # stat 指纹 → 缓存键
  → read_ocr_cache()           # 命中 → 直接返回
  → 未命中 → 调用视觉端点 → write_ocr_cache()
```

文档内嵌图不自动 OCR：图片块只返回路径，由模型显式 `read` 该图片触发。

### 文档读取流

```
ReadTool → 后缀命中 DOC_FORMATS
  → read_document_branch()
    → resolve_within() + runtime.read_bytes()
    → cache_dir()  # 内容 sha1 前 16 位
    → ensure_cache()  # 身份字段不符则整目录重建
    → _page_blocks()  # 缓存优先（text 可读则用 text，否则回退 vision）
    → render_blocks() / render_blocks_by_page()  # 超预算时给 after 游标或「下一页」
```

PDF 扫描页：`_ocr_pdf_page()` 在 `_PDFIUM_LOCK` 内渲染 JPEG，锁外经进程级 `Semaphore(8)` 调视觉模型转录，结果以文本块（`is_ocr=True`）落缓存；临时失败不落缓存。

`_vision_chat()` 是唯一的视觉调用出口，持 `_OCR_SEMAPHORE`。`finish_reason=length` 视为失败并返回错误、不落缓存（截断文本当完整页用会静默丢内容）。

docx 内嵌图按 `pic:spPr/a:xfrm/@rot` 的声明角度旋转后落盘：`word/media/` 存的是未旋转位图，Word 里的旋转只写在 xfrm 上。丢了这个信息，视觉模型会读到倒转 90° 的扫描件并猜造数值。EXIF 方向不做处理（vLLM 已按 EXIF 转置）。

xlsx/pptx 走同一约定：`structured_office_images()` 按 `sheet→drawing→media` / `presentation→slide→media` 的关系链取内嵌图，读 `xdr:pic`、`p:pic` 下的 `a:xfrm/@rot`（同为 1/60000 度）还原后落盘，以 image 块追加在锚点文本之后（`page` 为工作表号/幻灯片号）。锚点文本（`s#S!r#Rc#C`、`slide#N/tf#M`）不变，编辑工具依赖该定位。附件预览复用同一函数。

`grep` 命中同一批后缀时改走 `search_blocks()`，只扫已解析页（text 与 vision 两档每页取一个权威来源）并在响应里声明 `coverage`。

### 并发写入保护流

```
多个并发 EditTool/WriteTool 请求同一文件
  → with_file_mutation_queue(file_path)
    → 每文件 asyncio.Lock 串行化
    → 逐个执行写入操作
  → clear_mutation_queues()  # session 结束清理
```

## 对外接口

| 接口 | 类型 | 工具类 | 说明 |
|------|------|--------|------|
| `BashTool` (name=`bash`) | 工具 | write | 持久 PTY Bash 执行 |
| `ReadTool` (name=`read`) | 工具 | read | 分页文件读取 + read_epoch；文档按页 + 块坐标 |
| `WriteTool` (name=`write`) | 工具 | write | 文件写入 + 二进制阻断 |
| `EditTool` (name=`edit`) | 工具 | write | 字符串替换编辑 + 并发控制 |
| `ListDirectoryTool` (name=`ls`) | 工具 | read | 目录列表 |
| `GlobTool` (name=`glob`) | 工具 | read | 文件模式搜索 |
| `GrepTool` (name=`grep`) | 工具 | read | 文件内容搜索；文档后缀走块级检索 |
| `SubagentControlTool` (name=`subagent_control`) | 工具 | write | 子代理状态查询、中断与协作（main_only） |
| `AskUserQuestionTool` (name=`AskUserQuestion`) | 工具 | read | 用户问答交互 |
| `FileObserver` | 基础设施 | — | SHA-256 文件版本跟踪 |
| `with_file_mutation_queue()` | 基础设施 | — | 并发写入串行化 |

## 依赖关系

```
工具集模块
  ├── engine.runtime           — BashRuntime（bash/fs_list/fs_glob/fs_grep）
  ├── engine.security.sandbox  — 沙箱策略与路径校验
  ├── engine.services.review   — review registry（WriteTool/EditTool 集成）
  ├── schema.agent             — ToolContext, ToolResult
  ├── engine.security.gate     — ParallelTaskExtras / TaskRuntimeContext（ToolContext）
  ├── persistence.session      — session 存储（OCR 缓存路径）
  └── infra.settings           — 配置
```

## 配置项

| 配置 | 位置 | 说明 | 默认值 |
|------|------|------|--------|
| `BASH_DEFAULT_TIMEOUT_SECONDS` | `file_helpers.py` | Bash 默认超时 | `120.0` |
| `_READ_DEFAULT_PAGE_LINES` | `file_helpers.py` | ReadTool 分页行数 | `2000` |
| `_BASH_READBACK_MAX_CHARS` | `file_helpers.py` | Bash 回显最大字符数 | `200_000` |
| `MAX_LINE_LENGTH` | `file_tools.py` (ReadTool) | 单行最大长度 | `2000` |
| `blocking_budget_ms` | BashTool 参数 | 阻塞预算上限 | `15000` |
| `max_results` | GlobTool | 搜索结果上限 | `100` |
| `max_matches` | GrepTool | 匹配结果上限 | `250` |
