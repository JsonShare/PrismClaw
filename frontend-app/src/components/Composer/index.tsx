import { Text, Textarea, View } from '@tarojs/components'
import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import type { Chip } from '../../lib/chat/markers'
import { makeChip } from '../../lib/chat/markers'
import { expertGroups, listSkills, skillLabel, standaloneSkills } from '../../services/skills'
import type { SkillItem } from '../../services/skills'
import { pickLocalFiles, pickPhotos } from '../../services/uploads'
import type { LocalFile } from '../../services/uploads'
import { startVoice } from '../../services/voice'
import type { VoiceSession, VoiceState } from '../../services/voice'
import { useComposerStore } from '../../stores/useComposerStore'
import { toast } from '../../utils/toast'
import Icon from '../Icon'
import AddSheet from './AddSheet'
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
  streaming?: boolean
  disabled?: boolean
  /** 输入卡下方的一行（模式切换、工作区等），与输入区共用遮罩与安全区内边距 */
  footer?: ReactNode
  onSend: (payload: ComposerPayload) => Promise<void> | void
  onAbort?: () => void
}

const CHIP_ICON: Record<Chip['kind'], string> = {
  skill: 'hammer',
  plugin: 'blocks',
  file: 'at-sign',
}

const LIBRARY_TITLE = '技能与专家'
const LIBRARY_PLACEHOLDER = '搜索专家或技能'
const LIBRARY_EMPTY = '没有可用的技能或专家'

/** 插件 ID 转可读标题：medical-wellness → Medical Wellness（与 PC 端一致） */
function pluginTitle(name: string): string {
  return name
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1).toLowerCase())
    .join(' ')
}

export default function Composer({
  composerKey,
  placeholder,
  flat = false,
  streaming = false,
  disabled = false,
  footer,
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
  // 默认进入文字输入态，需要语音时点左侧按钮切换
  const [voiceMode, setVoiceMode] = useState(false)
  /** 按住说话时手指所处区域：idle=中间发送，cancel/edit=两侧 */
  const [holdState, setHoldState] = useState<'idle' | 'cancel' | 'edit'>('idle')
  const holdStateRef = useRef<'idle' | 'cancel' | 'edit'>('idle')
  const holdPointRef = useRef({ x: 0, y: 0 })
  const voiceIntentRef = useRef<'send' | 'edit' | 'cancel'>('send')
  const voiceRef = useRef<VoiceSession | null>(null)
  const voiceBaseRef = useRef('')
  const skillsCache = useRef<SkillItem[] | null>(null)
  const aliveRef = useRef(true)

  useEffect(
    () => () => {
      aliveRef.current = false
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

  const pickFile = async () => {
    try {
      const picked = await pickLocalFiles()
      if (!picked.length) return
      addFiles(composerKey, picked)
    } catch (error) {
      toast((error as Error).message || '选择文件失败')
    }
  }

  const pickPhotosFrom = async (from: 'camera' | 'album') => {
    try {
      const picked = await pickPhotos(from)
      if (!picked.length) return
      addFiles(composerKey, picked)
    } catch (error) {
      toast((error as Error).message || (from === 'camera' ? '拍照失败' : '选择图片失败'))
    }
  }

  const handleAdd = (kind: 'camera' | 'album' | 'files') => {
    setAddOpen(false)
    if (kind === 'files') void pickFile()
    else void pickPhotosFrom(kind)
  }

  const handleLibrary = () => {
    setAddOpen(false)
    setLibraryOpen(true)
  }

  const handlePick = (item: LibraryItem) => {
    addChip(composerKey, makeChip(item.chipKind ?? 'skill', item.id, item.label))
    setLibraryOpen(false)
  }

  /** 切到语音态：输入框换成「按住说话」，草稿作为识别结果前缀 */
  const enterVoice = () => {
    if (voiceState !== 'idle') return
    voiceBaseRef.current = draft
    setFocused(false)
    setVoiceMode(true)
  }

  const leaveVoice = () => {
    if (voiceState !== 'idle') {
      voiceRef.current?.stop()
      return
    }
    setVoiceMode(false)
  }

  const setHold = (next: 'idle' | 'cancel' | 'edit') => {
    holdStateRef.current = next
    setHoldState(next)
  }

  const startRecording = (event: { touches?: { clientX: number; clientY: number }[] }) => {
    if (!voiceMode || voiceState !== 'idle') return
    const touch = event.touches?.[0]
    if (touch) holdPointRef.current = { x: touch.clientX, y: touch.clientY }
    voiceIntentRef.current = 'send'
    setHold('idle')
    setVoiceState('connecting')
    voiceRef.current = startVoice({
      onStateChange: (state) => {
        if (aliveRef.current) setVoiceState(state)
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
          void onSend({ text: merged.trim(), chips, files })
            .then(() => reset(composerKey))
            .catch((error: Error) => toast(error.message || '发送失败'))
        }
      },
      onError: (message) => {
        if (!aliveRef.current) return
        setVoiceState('idle')
        voiceRef.current = null
        setVoiceMode(false)
        setHold('idle')
        toast(message)
      },
    })
  }

  const moveRecording = (event: { touches?: { clientX: number; clientY: number }[] }) => {
    if (voiceState === 'idle') return
    const touch = event.touches?.[0]
    if (!touch) return
    const dx = touch.clientX - holdPointRef.current.x
    const dy = touch.clientY - holdPointRef.current.y
    setHold(dy < -44 ? (dx < 0 ? 'cancel' : 'edit') : 'idle')
  }

  const endRecording = () => {
    if (voiceState === 'idle') return
    voiceIntentRef.current = holdStateRef.current
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
  // 输入中、发送中或流式输出时自动收起下方模式与工作区（切换 chat / 工作区），空态再展开
  const footerHidden =
    focused ||
    sending ||
    streaming ||
    draft.trim().length > 0 ||
    chips.length > 0 ||
    files.length > 0

  const handleSend = async () => {
    if (streaming) {
      onAbort?.()
      return
    }
    if (!canSend) return
    setSending(true)
    try {
      await onSend({ text: draft.trim(), chips, files })
      reset(composerKey)
    } catch (error) {
      toast((error as Error).message || '发送失败')
    } finally {
      setSending(false)
    }
  }

  return (
    <>
      <View className={`composer-wrap${flat ? ' is-flat' : ''}`}>
        {files.length ? (
          <View className='composer-attachments'>
            {files.map((file) => (
              <View className='composer-attachment' key={file.id}>
                <Icon name='file-text' size={14} color='#575B62' />
                <Text className='composer-attachment-name'>{file.name}</Text>
                <View
                  className='composer-attachment-remove'
                  onClick={() => removeFile(composerKey, file.id)}
                >
                  <Icon name='x' size={12} color='#838C9E' />
                </View>
              </View>
            ))}
          </View>
        ) : null}

        {chips.length ? (
          <View className='composer-chips'>
            {chips.map((chip) => (
              <View className='composer-chip' key={chip.id}>
                <Icon name={CHIP_ICON[chip.kind]} size={13} color='#2C59BC' />
                <Text className='composer-chip-text'>{chip.label}</Text>
                <View
                  className='composer-chip-remove'
                  onClick={() => removeChip(composerKey, chip.id)}
                >
                  <Icon name='x' size={11} color='#2C59BC' />
                </View>
              </View>
            ))}
          </View>
        ) : null}

        <View
          className={`composer-card${flat ? ' is-flat' : ''}${voiceMode ? ' is-voice' : ''}${
            !voiceMode && (focused || draft.trim().length > 0) ? ' is-expanded' : ''
          }`}
        >
          <View
            className='composer-round'
            onClick={() => (voiceMode ? leaveVoice() : enterVoice())}
          >
            <Icon name={voiceMode ? 'keyboard' : 'mic'} size={15} color='#111316' strokeWidth={2} />
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
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              onConfirm={handleSend}
            />
          )}

          {streaming ? (
            <View className='composer-round is-solid is-stop' onClick={handleSend}>
              <Icon name='square' size={13} color='#FFFFFF' strokeWidth={2.2} />
            </View>
          ) : (
            <View className='composer-round' onClick={() => setAddOpen(true)}>
              <Icon name='plus' size={17} color='#111316' strokeWidth={2} />
            </View>
          )}
        </View>

        {voiceState !== 'idle' ? (
          <View className='voice-overlay'>
            <View className={`voice-zone is-cancel${holdState === 'cancel' ? ' is-active' : ''}`}>
              <Text>取消发送</Text>
            </View>
            <View className={`voice-zone is-edit${holdState === 'edit' ? ' is-active' : ''}`}>
              <Text>去编辑</Text>
            </View>
            <Text className='voice-release'>{holdState === 'cancel' ? '松开 取消' : '松开 发送'}</Text>
            <View className='voice-dots'>
              {Array.from({ length: 22 }).map((_, index) => (
                <View className='voice-dot' key={index} />
              ))}
            </View>
          </View>
        ) : null}

        {footer ? (
          <View className={`composer-footer${footerHidden ? ' is-hidden' : ''}`}>{footer}</View>
        ) : null}
      </View>

      <AddSheet
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onPick={handleAdd}
        onLibrary={handleLibrary}
      />

      <LibrarySheet
        open={libraryOpen}
        title={LIBRARY_TITLE}
        placeholder={LIBRARY_PLACEHOLDER}
        emptyText={LIBRARY_EMPTY}
        load={loadLibrary}
        onPick={handlePick}
        onClose={() => setLibraryOpen(false)}
      />
    </>
  )
}
