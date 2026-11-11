/* ─── File Change Review ───
   统一的"文件变更"模型：代码/文本/配置与办公文件（docx/xlsx/pptx）
   共用同一套 hunks 渲染（绿增/红删），不区分内部表示。
   后端经 ToolResult.metadata["file_change"] 注入。 */

export type FileChangeKind =
  | 'text'
  | 'code'
  | 'config'
  | 'docx'
  | 'xlsx'
  | 'pptx'
  | 'image'
  | 'binary'

export type DiffHunkType = 'equal' | 'insert' | 'delete'

export interface DiffHunk {
  type: DiffHunkType
  lines: string[]
}

export interface OfficeChangeDetail {
  ref: string
  op:
    | 'replace_paragraph'
    | 'insert_after'
    | 'delete_paragraph'
    | 'replace_run'
    | 'set_cell'
    | 'insert_row'
    | 'delete_row'
    | 'insert_column'
    | 'delete_column'
    | 'set_text_frame'
    | 'delete_text_frame'
  before: string
  after: string
  inserted_chars: number
  deleted_chars: number
}

/** 一条办公文件编辑操作（与后端 office_edit 的 op 一一对应）。前端按勾选子集回传，
 *  由后端从原始备份 blob 重新物化仅被接受的改动。 */
export interface OfficeOperation {
  op: string
  ref: string
  text?: string
  value?: unknown
  values?: unknown[]
  table?: number
  row?: number
  col?: number
  [key: string]: unknown
}

export interface FileChange {
  path: string
  name: string
  kind: FileChangeKind
  insertions: number
  deletions: number
  hunks: DiffHunk[]
  /** 办公文件：用自然语言说明结构性改动（如「新增第 3.2 条段落」） */
  note?: string
  /** 编辑前的备份引用（blob 路径），用于「拒绝」时调 API 还原 */
  before_ref?: string
  /** 编辑前内容 SHA-256，用于完整性校验 */
  before_sha?: string
  /** diff 被截断时为 true（大文件折叠了 equal hunks） */
  truncated?: boolean
  /** `xlsx/pptx` 旧链路的逐处结构化改动明细。`.docx` 新链路不再使用该字段。 */
  officeDetail?: OfficeChangeDetail[]
  /** `xlsx/pptx` 旧链路的原始操作列表；与 officeDetail 顺序一致。 */
  operations?: OfficeOperation[]
  /** `.docx` 原生审阅模式标记；新链路固定为 `docx-native`。 */
  review_mode?: 'docx-native' | null
  /** `.docx` 原生 tracked-changes artifact 的 blob ref。 */
  review_docx_ref?: string
  /** `.docx` 原生 tracked-changes artifact 的 SHA-256。 */
  review_docx_sha?: string
  /** `.docx` 原生审阅工件中的待审修订数量。 */
  pending_changes?: number
  /** 该变更所属消息的 id，用于持久化审阅决策（后端投影时注入）。 */
  message_id?: string
  /** 持久化的审阅决策；`undefined` 表示待审。刷新后由后端 `file_review` 事件回放。 */
  reviewed?: 'confirmed' | 'rejected'
}
