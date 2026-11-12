import { create } from 'zustand'

// 执行档位：与后端 RunRequest.effort 对齐。
// quick/standard/deep 分别决定 steps / tool_calls / 墙钟预算（见 agent.yaml effort 档位）。
export type EffortLevel = 'quick' | 'standard' | 'deep'

const STORAGE_KEY = 'prismclaw.effort'

function loadEffort(): EffortLevel {
  try {
    const v = localStorage.getItem(STORAGE_KEY)
    if (v === 'quick' || v === 'standard' || v === 'deep') return v
  } catch {
    /* localStorage 不可用时回退默认 */
  }
  return 'standard'
}

interface EffortState {
  effort: EffortLevel
}

interface EffortActions {
  setEffort: (effort: EffortLevel) => void
}

export type EffortStore = EffortState & EffortActions

export const useEffortStore = create<EffortStore>((set) => ({
  effort: loadEffort(),
  setEffort: (effort) => {
    try {
      localStorage.setItem(STORAGE_KEY, effort)
    } catch {
      /* 忽略持久化失败 */
    }
    set({ effort })
  },
}))
