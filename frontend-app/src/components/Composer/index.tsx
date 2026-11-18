import { Image, ScrollView, Text, Textarea, View } from '@tarojs/components'
import type { ITouchEvent } from '@tarojs/components'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { Chip } from '../../lib/chat/markers'
import { CHIP_ICONS, makeChip } from '../../lib/chat/markers'
import { expertGroups, listSkills, skillLabel, standaloneSkills } from '../../services/skills'
import type { SkillItem } from '../../services/skills'
import { MAX_ATTACHMENTS, pickLocalFiles, pickPhotos } from '../../services/uploads'
import type { LocalFile } from '../../services/uploads'
import { prewarmMicrophone, releaseMicrophone, startVoice } from '../../services/voice'
import type { VoiceSession, VoiceState } from '../../services/voice'
import { useComposerStore } from '../../stores/useComposerStore'
import { toast } from '../../utils/toast'
import Icon from '../Icon'
import AddSheet from './AddSheet'
import type { AddAction } from './AddSheet'
import LibrarySheet from './LibrarySheet'
import type { LibraryItem } from './LibrarySheet'

export interface ComposerPayload {
  text: string
  chips: Chip[]
  files: LocalFile[]
}

interface ComposerProps {
  /** 输入态归属：会话 id，首页传 'home' */
  composerKey: string
  placeholder: string
  /** 首页落地页的扁平卡片样式 */
  flat?: boolean
  /** 展开态的占位文案（默认为 placeholder） */
  expandedPlaceholder?: string
  streaming?: boolean
  disabled?: boolean
  /** 输入卡下方的一行（工作区等工具），与输入区共用遮罩与安全区内边距 */
  footer?: ReactNode
  /** 聚焦 / 展开状态变化（首页据此隐藏推荐卡片与 chips） */
  onFocusChange?: (focused: boolean) => void
  onSend: (payload: ComposerPayload) => Promise<void> | void
  onAbort?: () => void
}

/** 插件 ID 转可读标题：medical-wellness → Medical Wellness（与 PC 端一致） */
function pluginTitle(name: string): string {
  return name
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
}

function fileExtension(name: string): string {
  const parts = name.split('.')
  return parts.length > 1 ? (parts.pop() || 'file').toLowerCase() : 'file'
}

/** 文件卡角标：Office 文档用品牌首字母，其余取扩展名首字母 */
const FILE_BADGE: Record<string, string> = {
  doc: 'W',
  docx: 'W',
  xls: 'X',
  xlsx: 'X',
  ppt: 'P',
  pptx: 'P',
}

function fileBadge(extension: string): string {
  return FILE_BADGE[extension] || extension.charAt(0).toUpperCase()
}

function fileSize(size: number): string {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function AttachmentItem({ file, onRemove }: { file: LocalFile; onRemove: () => void }) {
  const isImage = file.mimeType.startsWith('image/')
  const extension = fileExtension(file.name)
  const [preview, setPreview] = useState(
    isImage && typeof file.source === 'string' ? file.source : '',
  )

  useEffect(() => {
    if (!isImage || typeof file.source === 'string') return
    const url = URL.createObjectURL(file.source)
    setPreview(url)
    return () => URL.revokeObjectURL(url)
  }, [file.source, isImage])

  return (
    <View className={`composer-attachment${isImage ? ' is-image' : ' is-file'}`}>
      {isImage ? (
        <View className='composer-attachment-thumb'>
          {preview ? (
            <Image className='composer-attachment-image' src={preview} mode='aspectFill' />
          ) : null}
        </View>
      ) : (
        <>
          <View className='composer-attachment-doc'>
            <Text className='composer-attachment-ext'>{fileBadge(extension)}</Text>
          </View>
          <View className='composer-attachment-info'>
            <Text className='composer-attachment-name'>{file.name}</Text>
            <Text className='composer-attachment-meta'>
              {extension}·{fileSize(file.size)}
            </Text>
          </View>
        </>
      )}
      <View className='composer-attachment-remove' onClick={onRemove}>
        <Icon name='x' size={13} color='#FFFFFF' strokeWidth={2.2} />
      </View>
    </View>
  )
}

export default function Composer({
  composerKey,
  placeholder,
  flat = false,
  expandedPlaceholder,
  streaming = false,
  disabled = false,
  footer,
  onFocusChange,
  onSend,
  onAbort,
}: ComposerProps) {
  const draft = useComposerStore((s) => s.drafts[composerKey] ?? '')
  const chips = useComposerStore((s) => s.chips[composerKey]) ?? []
  const files = useComposerStore((s) => s.files[composerKey]) ?? []
  const setDraft = useComposerStore((s) => s.setDraft)
  const addChip = useComposerStore((s) => s.addChip)
  const removeChip = useComposerStore((s) => s.removeChip)
  const addFiles = useComposerStore((s) => s.addFiles)
  const removeFile = useComposerStore((s) => s.removeFile)
  const reset = useComposerStore((s) => s.reset)

  const [libraryOpen, setLibraryOpen] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [sending, setSending] = useState(false)
  const [focused, setFocused] = useState(false)
  const [voiceState, setVoiceState] = useState<VoiceState | 'idle'>('idle')
  const [voiceWarning, setVoiceWarning] = useState('')
  // 默认进入文字输入态，需要语音时点左侧按钮切换
  const [voiceMode, setVoiceMode] = useState(false)
  /** 按住说话时手指所处区域：idle=中间发送，cancel/edit=两侧 */
  const [holdState, setHoldState] = useState<'idle' | 'cancel' | 'edit'>('idle')
  /** 录音声波：最近 24 帧的能量（0~1），按帧左移，形成横向律动 */
  const [levels, setLevels] = useState<number[]>(() => new Array(24).fill(0))
  const holdStateRef = useRef<'idle' | 'cancel' | 'edit'>('idle')
  const holdPointRef = useRef({ x: 0, y: 0 })
  const voiceIntentRef = useRef<'send' | 'edit' | 'cancel'>('send')
  const voiceRef = useRef<VoiceSession | null>(null)
  const voiceBaseRef = useRef('')
  const voiceWarningTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const skillsCache = useRef<SkillItem[] | null>(null)
  const aliveRef = useRef(true)
  /** blur 后延迟收起：点卡片内按钮会先失焦，立即收起会让卡片跳成胶囊 */
  const blurTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  /** 系统文件选择器打开期间页面会失焦，此时不要收起卡片 */
  const pickingRef = useRef(false)

  const cancelBlur = () => {
    if (blurTimer.current) {
      clearTimeout(blurTimer.current)
      blurTimer.current = null
    }
  }

  const cancelVoiceWarning = () => {
    if (voiceWarningTimer.current) {
      clearTimeout(voiceWarningTimer.current)
      voiceWarningTimer.current = null
    }
  }

  useEffect(
    () => () => {
      aliveRef.current = false
      cancelBlur()
      cancelVoiceWarning()
      voiceRef.current?.stop()
    },
    [],
  )

  const loadSkills = useCallback(async () => {
    if (!skillsCache.current) skillsCache.current = await listSkills()
    return skillsCache.current
  }, [])

  const loadLibrary = useCallback(
    async (query: string): Promise<LibraryItem[]> => {
      const skills = await loadSkills()
      const q = query.toLowerCase()
      const experts: LibraryItem[] = expertGroups(skills)
        .filter(
          (group) =>
            !q ||
            group.name.toLowerCase().includes(q) ||
            pluginTitle(group.name).toLowerCase().includes(q) ||
            group.description.toLowerCase().includes(q) ||
            group.skills.some((skill) => skillLabel(skill).toLowerCase().includes(q)),
        )
        .map((group) => ({
          id: group.name,
          label: pluginTitle(group.name),
          desc: group.description,
          emoji: group.skills.find((skill) => skill.emoji)?.emoji,
          section: '专家',
          chipKind: 'plugin',
        }))
      const singles: LibraryItem[] = standaloneSkills(skills)
        .filter(
          (skill) =>
            !q ||
            skill.name.toLowerCase().includes(q) ||
            (skill.nameZh ?? '').toLowerCase().includes(q) ||
            skill.description.toLowerCase().includes(q),
        )
        .map((skill) => ({
          id: skill.id,
          label: skillLabel(skill),
          desc: skill.description,
          emoji: skill.emoji,
          section: '技能',
          chipKind: 'skill',
        }))
      return [...experts, ...singles]
    },
    [loadSkills],
  )

  /** 取附件：拍摄 / 相册 / 文件，选到后统一并入当前输入态 */
  const runPick = async (action: 'camera' | 'album' | 'file') => {
    cancelBlur()
    if (pickingRef.current) return
    const remaining = MAX_ATTACHMENTS - files.length
    if (remaining <= 0) {
      toast(`最多添加 ${MAX_ATTACHMENTS} 个附件`)
      return
    }
    pickingRef.current = true
    try {
      const picked = action === 'file'
        ? await pickLocalFiles(remaining)
        : await pickPhotos(action, remaining)
      if (!picked.length) return
      addFiles(composerKey, picked)
    } catch (error) {
      toast((error as Error).message || (action === 'file' ? '选择文件失败' : '选择图片失败'))
    } finally {
      pickingRef.current = false
    }
  }

  /** 添加面板：前三个走系统选择器，最后一个进入技能面板 */
  const handleAddPick = (action: AddAction) => {
    setAddOpen(false)
    if (action === 'library') {
      setLibraryOpen(true)
      return
    }
    void runPick(action)
  }

  /** ⊕ 打开「添加」面板：拍摄 / 相册 / 文件 + 添加技能或套件 */
  const openAdd = () => {
    cancelBlur()
    setFocused(true)
    onFocusChange?.(true)
    setAddOpen(true)
  }

  /** 关闭面板时若输入框本就未聚焦，卡片回到胶囊态 */
  const closeSheet = () => {
    setAddOpen(false)
    setLibraryOpen(false)
    setFocused(false)
    onFocusChange?.(false)
  }

  // 组件卸载（切页）时释放麦克风，避免权限指示灯常亮
  useEffect(() => () => releaseMicrophone(), [])
  // 任何路径退出语音态（点键盘、去编辑、识别失败）都释放缓存的麦克风
  useEffect(() => {
    if (!voiceMode) releaseMicrophone()
  }, [voiceMode])
  // 录音结束把声波收平，回到一排小点
  useEffect(() => {
    if (voiceState === 'idle') setLevels(new Array(24).fill(0))
  }, [voiceState])

  /** 技能/专家单选：选中即收起面板，保留已选 chips 不覆盖 */
  const handlePick = (item: LibraryItem) => {
    const chip = makeChip(item.chipKind ?? 'skill', item.id, item.label)
    if (!chips.some((current) => current.id === chip.id)) {
      addChip(composerKey, chip)
    }
    setLibraryOpen(false)
  }

  /** 切到语音态：输入框换成「按住说话」，草稿作为识别结果前缀 */
  const enterVoice = () => {
    if (voiceState !== 'idle') return
    cancelBlur()
    voiceBaseRef.current = draft
    setFocused(false)
    // 在用户手势内先要一次麦克风，之后整段语音态复用同一条流，不再逐次弹权限
    prewarmMicrophone()
    // 语音态与文字态的图标位置不同：同一次 tap 若在本任务内换布局，
    // 落点会被新出现的相机按钮再命中一次（点击穿透），因此延到事件分发后再切换
    setTimeout(() => setVoiceMode(true), 0)
  }

  const leaveVoice = () => {
    if (voiceState !== 'idle') {
      voiceRef.current?.stop()
      return
    }
    releaseMicrophone()
    cancelVoiceWarning()
    setVoiceWarning('')
    // 同理：返回文字态时该位置会换回声波按钮，避免键盘键的点击穿透到声波上
    setTimeout(() => setVoiceMode(false), 0)
  }

  const setHold = (next: 'idle' | 'cancel' | 'edit') => {
    holdStateRef.current = next
    setHoldState(next)
  }

  /** 文字与语音共用发送闭环：成功才清空，失败保留草稿，期间禁止重复发送 */
  const deliver = async (payload: ComposerPayload) => {
    if (sending) return
    setSending(true)
    try {
      await onSend(payload)
      reset(composerKey)
      voiceBaseRef.current = ''
    } catch (error) {
      toast((error as Error).message || '发送失败')
    } finally {
      setSending(false)
    }
  }

  const startRecording = (event: ITouchEvent) => {
    if (!voiceMode || voiceState !== 'idle' || sending) return
    const touch = event.touches?.[0]
    if (touch) holdPointRef.current = { x: touch.clientX, y: touch.clientY }
    // 每次按住都读取最新草稿，避免上一次已发送的识别文本被再次拼入
    voiceBaseRef.current = draft
    voiceIntentRef.current = 'send'
    setHold('idle')
    setLevels(new Array(24).fill(0))
    cancelVoiceWarning()
    setVoiceWarning('')
    setVoiceState('connecting')
    voiceRef.current = startVoice({
      onStateChange: (state) => {
        if (aliveRef.current) setVoiceState(state)
      },
      // RMS 通常只有 0.02~0.2：先放大再开方，弱声也能推出明显起伏
      onLevel: (rms) => {
        if (!aliveRef.current) return
        const level = Math.min(1, Math.pow(Math.min(1, rms * 12), 0.75))
        // 取「本帧与上一帧衰减值」的较大者，避免 10Hz 采样导致柱高忽上忽下
        setLevels((prev) => {
          const last = prev[prev.length - 1] ?? 0
          return [...prev.slice(1), Math.max(level, last * 0.68)]
        })
      },
      onPartial: (text) => {
        if (!aliveRef.current) return
        const base = voiceBaseRef.current
        setDraft(composerKey, base ? `${base} ${text}` : text)
      },
      // 松手后按落点决定：取消 / 去编辑 / 直接发送
      onFinal: (text) => {
        if (!aliveRef.current) return
        const intent = voiceIntentRef.current
        const base = voiceBaseRef.current
        setVoiceState('idle')
        voiceRef.current = null
        setHold('idle')
        if (intent === 'cancel') {
          setDraft(composerKey, base)
          return
        }
        // 去编辑：回到键盘态便于修改；发送则保持语音态，方便连续说
        if (intent === 'edit') setVoiceMode(false)
        const merged = text ? (base ? `${base} ${text}` : text) : base
        setDraft(composerKey, merged)
        if (intent === 'send' && merged.trim()) {
          void deliver({ text: merged.trim(), chips, files })
        }
      },
      onError: (message) => {
        if (!aliveRef.current) return
        setVoiceState('idle')
        voiceRef.current = null
        setHold('idle')
        // 连接失败时保持语音态并展示错误，避免浮层一闪而过用户看不到反馈
        setVoiceWarning(message)
        // 3 秒后自动清除错误并退出语音态；重新按住或主动退出时取消旧计时器
        cancelVoiceWarning()
        voiceWarningTimer.current = setTimeout(() => {
          voiceWarningTimer.current = null
          if (aliveRef.current) {
            setVoiceWarning('')
            setVoiceMode(false)
          }
        }, 3000)
      },
    })
  }

  const moveRecording = (event: ITouchEvent) => {
    if (voiceState === 'idle') return
    const touch = event.touches?.[0]
    if (!touch) return
    const dx = touch.clientX - holdPointRef.current.x
    const dy = touch.clientY - holdPointRef.current.y
    setHold(dy < -44 ? (dx < 0 ? 'cancel' : 'edit') : 'idle')
  }

  const endRecording = () => {
    if (voiceState === 'idle') return
    voiceIntentRef.current = holdStateRef.current === 'idle' ? 'send' : holdStateRef.current
    setHold('idle')
    voiceRef.current?.stop()
  }

  /** 手势被系统打断时按取消处理，避免误发 */
  const cancelRecording = () => {
    if (voiceState === 'idle') return
    voiceIntentRef.current = 'cancel'
    setHold('idle')
    voiceRef.current?.stop()
  }

  const canSend = !disabled && !sending && (draft.trim().length > 0 || chips.length > 0 || files.length > 0)
  // 首页扁平形态：聚焦、已有文字或待选附件时展开成白色大卡片
  const expanded = flat && !voiceMode && (focused || draft.trim().length > 0 || files.length > 0 || chips.length > 0)
  const attachmentsInside = flat && files.length > 0
  // H5 下 Taro 给 textarea 写死内联高度，长文本会裁掉半行，这里按内容手动撑高（最多 4 行）
  const cardRef = useRef<any>(null)
  const syncTextareaHeight = useCallback(() => {
    if (process.env.TARO_ENV !== 'h5') return
    const root = cardRef.current as HTMLElement | null
    const el = root?.querySelector?.('textarea.taro-textarea') as HTMLTextAreaElement | null
    if (!el) return
    el.style.height = 'auto'
    // 空内容时按一行行高兜底：浏览器在 auto 测量下会返回双倍行高，占位文字会掉到上半行
    const next = el.value.trim() ? Math.min(el.scrollHeight, 96) : 24
    el.style.height = `${next}px`
    el.style.overflowY = el.scrollHeight > 96 ? 'auto' : 'hidden'
  }, [])

  useEffect(() => {
    if (flat && expanded) requestAnimationFrame(syncTextareaHeight)
  }, [flat, expanded, draft, syncTextareaHeight])
  const chipNodes = chips.map((chip) => (
    <View className='composer-chip' key={chip.id}>
      <Icon name={CHIP_ICONS[chip.kind]} size={14} color='#111316' />
      <Text className='composer-chip-text'>{chip.label}</Text>
      <View
        className='composer-chip-remove'
        onClick={() => removeChip(composerKey, chip.id)}
      >
        <Icon name='x' size={11} color='#6B7280' />
      </View>
    </View>
  ))
  const attachmentList = files.length ? (
    <ScrollView className='composer-attachments' scrollX showScrollbar={false}>
      <View className='composer-attachments-track'>
        {files.map((file) => (
          <AttachmentItem
            file={file}
            key={file.id}
            onRemove={() => removeFile(composerKey, file.id)}
          />
        ))}
      </View>
    </ScrollView>
  ) : null

  /** 附件直达入口：展开态底部一行，相册 / 文件 / 技能各自点开即用 */
  const attachActions = (flatButtons: boolean, withLibrary = true) => {
    const buttonClass = `composer-round${flatButtons ? ' is-flat' : ''}`
    const iconSize = flatButtons ? 20 : 16
    return (
      <View className='composer-attach-group'>
        <View className={buttonClass} onClick={() => void runPick('album')}>
          <Icon name='image' size={iconSize} color='#111316' strokeWidth={1.8} />
        </View>
        <View className={buttonClass} onClick={() => void runPick('file')}>
          <Icon name='folder-up' size={iconSize} color='#111316' strokeWidth={1.8} />
        </View>
        {withLibrary ? (
          <View className={buttonClass} onClick={() => setLibraryOpen(true)}>
            <Icon name='puzzle' size={iconSize} color='#111316' strokeWidth={1.8} />
          </View>
        ) : null}
      </View>
    )
  }

  const handleFocus = () => {
    cancelBlur()
    setFocused(true)
    onFocusChange?.(true)
  }

  const handleBlur = () => {
    cancelBlur()
    // 选择器/相册还在前台时失焦属于正常流程，保持卡片不动
    if (pickingRef.current) return
    blurTimer.current = setTimeout(() => {
      blurTimer.current = null
      setFocused(false)
      onFocusChange?.(false)
    }, 180)
  }

  // 发送中或流式输出时收起下方工具行；发出首条消息后由 started 控制永久隐藏
  const footerHidden = sending || streaming

  const handleSend = async () => {
    if (streaming) {
      onAbort?.()
      return
    }
    if (!canSend) return
    await deliver({ text: draft.trim(), chips, files })
  }

  return (
    <>
      <View className={`composer-wrap${flat ? ' is-flat' : ''}`}>
        {attachmentsInside ? null : attachmentList}

        {!flat && chips.length ? <View className='composer-chips'>{chipNodes}</View> : null}

        <View
          ref={cardRef}
          className={`composer-card${flat ? ' is-flat' : ''}${voiceMode ? ' is-voice' : ''}${
            expanded ? ' is-expanded' : ''
          }${streaming ? ' is-streaming' : ''}${attachmentsInside ? ' has-attachments' : ''}`}
        >
          {attachmentsInside ? attachmentList : null}
          {attachmentsInside ? <View className='composer-divider' /> : null}
          {flat ? (
            voiceMode ? (
              <View className='composer-voice-row'>
                <View className='composer-attach-group'>
                  <View className='composer-round' onClick={openAdd}>
                    <Icon name='plus' size={15} color='#111316' strokeWidth={2.2} />
                  </View>
                </View>
                <View
                  className='voice-hold is-flat'
                  onTouchStart={startRecording}
                  onTouchMove={moveRecording}
                  onTouchEnd={endRecording}
                  onTouchCancel={cancelRecording}
                >
                  <Text>{voiceState === 'finishing' ? '识别中…' : '按住说话'}</Text>
                </View>
                <View className='composer-flat-icon is-keyboard' onClick={leaveVoice}>
                  <Icon name='keyboard' size={24} color='#111316' strokeWidth={1.7} />
                </View>
              </View>
            ) : (
              <>
                <View className='composer-input-row'>
                  {chips.length ? chipNodes : null}
                  <Textarea
                    className='composer-textarea'
                    value={draft}
                    maxlength={-1}
                    autoHeight={expanded}
                    confirmType='send'
                    placeholder={expanded ? expandedPlaceholder || placeholder : placeholder}
                    placeholderClass='composer-placeholder'
                    onInput={(event) => {
                      setDraft(composerKey, event.detail.value)
                      requestAnimationFrame(syncTextareaHeight)
                    }}
                    onFocus={handleFocus}
                    onBlur={handleBlur}
                    onConfirm={handleSend}
                  />
                </View>
                <View className='composer-flat-foot'>
                  <View className='composer-attach-group'>
                    <View className='composer-round' onClick={openAdd}>
                      <Icon name='plus' size={15} color='#111316' strokeWidth={2.2} />
                    </View>
                  </View>
                  <View className='composer-flat-icons'>
                    <View className='composer-flat-icon is-audio' onClick={enterVoice}>
                      <Icon
                        name='audio-wave'
                        size={18}
                        color='#111316'
                        strokeWidth={1.8}
                      />
                    </View>
                    <View
                      className={`composer-stop${!streaming && !canSend ? ' is-idle' : ''}`}
                      onClick={handleSend}
                    >
                      {streaming ? (
                        <View className='composer-stop-square' />
                      ) : (
                        <Icon name='arrow-up' size={17} color='#FFFFFF' strokeWidth={2.4} />
                      )}
                    </View>
                  </View>
                </View>
              </>
            )
          ) : (
            <>
              <View
                className='composer-round'
                onClick={() => (voiceMode ? leaveVoice() : enterVoice())}
              >
                <Icon name={voiceMode ? 'keyboard' : 'mic'} size={19} color='#111316' strokeWidth={2} />
              </View>

              {voiceMode ? (
                <View
                  className='voice-hold'
                  onTouchStart={startRecording}
                  onTouchMove={moveRecording}
                  onTouchEnd={endRecording}
                  onTouchCancel={cancelRecording}
                >
                  <Text>{voiceState === 'finishing' ? '识别中…' : '按住说话'}</Text>
                </View>
              ) : (
                <Textarea
                  className='composer-textarea'
                  value={draft}
                  autoHeight
                  maxlength={-1}
                  confirmType='send'
                  placeholder={placeholder}
                  placeholderClass='composer-placeholder'
                  onInput={(event) => setDraft(composerKey, event.detail.value)}
                  onFocus={handleFocus}
                  onBlur={handleBlur}
                  onConfirm={handleSend}
                />
              )}

              {streaming ? (
                <View className='composer-round is-solid is-stop' onClick={handleSend}>
                  <Icon name='square' size={13} color='#FFFFFF' strokeWidth={2.2} />
                </View>
              ) : (
                attachActions(false)
              )}
            </>
          )}
        </View>

        {voiceState !== 'idle' || voiceWarning ? (
          <View className='voice-overlay'>
            <Text className={`voice-release${voiceWarning ? ' is-error' : ''}`}>
              {voiceWarning ? voiceWarning : holdState === 'cancel' ? '松开 取消' : '松开 发送'}
            </Text>
            <View className={`voice-zone is-cancel${holdState === 'cancel' ? ' is-active' : ''}`}>
              <Text>取消</Text>
            </View>
            <View className={`voice-zone is-edit${holdState === 'edit' ? ' is-active' : ''}`}>
              <Text>去编辑</Text>
            </View>
            <View className='voice-dots'>
              {levels.map((level, index) => (
                <View
                  className='voice-bar'
                  key={index}
                  style={{ height: `${3 + Math.round(level * 29)}px` }}
                />
              ))}
            </View>
          </View>
        ) : null}

        {footer ? (
          <View className={`composer-footer${footerHidden ? ' is-hidden' : ''}`}>{footer}</View>
        ) : null}
      </View>

      <AddSheet open={addOpen} onPick={handleAddPick} onClose={closeSheet} />

      <LibrarySheet
        open={libraryOpen}
        selectedIds={chips.map((chip) => chip.id)}
        load={loadLibrary}
        onPick={handlePick}
        onClose={closeSheet}
      />
    </>
  )
}
