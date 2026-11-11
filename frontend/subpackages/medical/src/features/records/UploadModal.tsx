/**
 * 智能病历 · 上传弹层
 *
 * 两种入口，同一弹层：
 *   1) 页头「上传病历」——不指定患者，上传后由 AI 识别归属，识别不出进「待指派」；
 *   2) 患者页内「上传报告」——带上当前患者，归属就此确定，解析只做对账
 *      （文内姓名对不上则标「需人工核验」，不偷偷改归属）。
 *
 * 选择文件 → 上传 → 立即关闭。真实解析进度由全局 ParseQueueFloat 展示。
 * 关闭入口：右上角 X、点击遮罩、Esc。
 */

import { useEffect, useRef, useState } from 'react'
import { Upload, X } from 'lucide-react'
import { Panel, useToast } from '../../components'
import { uploadRecords } from '../../api/client'

interface UploadModalProps {
  open: boolean
  onClose: () => void
  /** 上传成功后刷新列表 */
  onUploaded?: () => void
  /** 页内上传时选定的患者；缺省 = 全局上传（归属交给 AI 识别） */
  patient?: { id: string; name: string } | null
}

export function UploadModal({ open, onClose, onUploaded, patient }: UploadModalProps) {
  const { toast } = useToast()
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) {
      setUploading(false)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  /** 上传文件，成功后立即关闭弹窗，由 ParseQueueFloat 展示真实解析进度 */
  const doUpload = async (files: File[]) => {
    if (files.length === 0) return
    setUploading(true)

    try {
      const res = await uploadRecords(files, patient?.id)
      const count = res.reportIds.length
      toast(
        patient
          ? `已上传 ${count} 份，归档至 ${patient.name}，正在解析`
          : `病历上传成功，${count} 份已进入解析`,
        'success',
      )
      // 通知全局解析队列浮层立即拉取
      window.dispatchEvent(new CustomEvent('medical:upload-complete'))
      onUploaded?.()
      onClose()
    } catch {
      toast('上传失败，请稍后重试', 'error')
      setUploading(false)
    }
  }

  const handleFiles = (files: FileList | null) => {
    if (!files || files.length === 0) return
    void doUpload(Array.from(files))
  }

  return (
    <div className="mw-overlay" onClick={onClose}>
      <Panel
        className="max-w-md w-full overflow-hidden p-6"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="上传病历"
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-[length:var(--fs-lg)] font-bold mw-strong">
            {patient ? '上传报告' : '上传病历'}
          </h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full p-1.5 mw-faint hover:bg-[var(--c-surface-3)] transition-colors cursor-pointer"
            aria-label="关闭"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* 页内上传：把「归给谁」写在最前面，避免上传完还在猜报告去哪了 */}
        {patient && !uploading && (
          <div className="upl-target">
            <span className="upl-target-label">归档至</span>
            <span className="upl-target-name">{patient.name}</span>
          </div>
        )}

        {/* 上传中：显示加载态 */}
        {uploading ? (
          <div className="flex flex-col items-center justify-center py-8">
            <span className="mw-pqf-spinner" style={{ width: 24, height: 24, borderWidth: 2.5 }} />
            <p className="mt-3 text-[length:var(--fs-sm)] mw-muted">正在上传...</p>
          </div>
        ) : (
          <>
            <div
              onClick={() => fileInputRef.current?.click()}
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault()
                const files = Array.from(e.dataTransfer.files)
                void doUpload(files)
              }}
              className="mw-glass rounded-lg mw-dropzone flex flex-col items-center justify-center p-10 cursor-pointer mw-glass-hover"
            >
              <div className="h-10 w-10 rounded-lg bg-[var(--c-accent-soft)] flex items-center justify-center">
                <Upload className="h-4 w-4 text-[var(--c-accent)]" />
              </div>
              <p className="mt-2.5 text-[length:var(--fs-md)] font-medium mw-strong">拖入文件，或点击选择</p>
              <p className="mt-1 text-[length:var(--fs-sm)] mw-faint">
                支持 PDF / 图片 / 批量，单次最多 20 份
              </p>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                accept=".pdf,.doc,.docx,.dcm,.jpg,.jpeg,.png,.txt"
                onChange={(e) => handleFiles(e.target.files)}
              />
            </div>

            <p className="text-[length:var(--fs-xs)] mw-faint mt-4 px-1 leading-relaxed">
              {patient
                ? '报告直接归入该患者，不再走自动归属。若报告内姓名与所选患者不一致，会标记「需人工核验」。'
                : '不指定患者也可以上传。系统会读取报告内容自动识别归属，识别不出的会进入「待指派」等你确认。'}
            </p>
          </>
        )}
      </Panel>
    </div>
  )
}
