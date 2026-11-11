import { create } from 'zustand'

import type { Chip } from '../lib/chat/markers'
import type { LocalFile } from '../services/uploads'

/** 首页尚未创建会话时的输入态归属 */
export const HOME_COMPOSER_KEY = 'home'

interface ComposerStore {
  drafts: Record<string, string>
  chips: Record<string, Chip[]>
  files: Record<string, LocalFile[]>
  setDraft: (key: string, text: string) => void
  addChip: (key: string, chip: Chip) => void
  removeChip: (key: string, chipId: string) => void
  addFiles: (key: string, files: LocalFile[]) => void
  removeFile: (key: string, fileId: string) => void
  reset: (key: string) => void
}

const EMPTY: Record<string, never> = {}

/**
 * 输入态按会话隔离。此前草稿是全局单例，切换会话/首页原地对话时
 * 输入内容会串到另一个会话，这里以 sessionId（首页用 'home'）为键。
 */
export const useComposerStore = create<ComposerStore>((set, get) => ({
  drafts: EMPTY,
  chips: EMPTY,
  files: EMPTY,

  setDraft: (key, text) => set({ drafts: { ...get().drafts, [key]: text } }),

  addChip: (key, chip) => {
    const current = get().chips[key] ?? []
    if (current.some((item) => item.id === chip.id)) return
    set({ chips: { ...get().chips, [key]: current.concat(chip) } })
  },

  removeChip: (key, chipId) => {
    const current = get().chips[key] ?? []
    set({ chips: { ...get().chips, [key]: current.filter((item) => item.id !== chipId) } })
  },

  addFiles: (key, files) => {
    const current = get().files[key] ?? []
    const seen = new Set(current.map((item) => item.id))
    const merged = current.concat(files.filter((item) => !seen.has(item.id)))
    // 与 PC 端一致：单条消息最多 10 个附件
    set({ files: { ...get().files, [key]: merged.slice(0, 10) } })
  },

  removeFile: (key, fileId) => {
    const current = get().files[key] ?? []
    set({ files: { ...get().files, [key]: current.filter((item) => item.id !== fileId) } })
  },

  reset: (key) => {
    const drafts = { ...get().drafts }
    const chips = { ...get().chips }
    const files = { ...get().files }
    delete drafts[key]
    delete chips[key]
    delete files[key]
    set({ drafts, chips, files })
  },
}))
