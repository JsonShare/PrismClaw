import { Input, Picker, ScrollView, Text, Textarea, View } from '@tarojs/components'
import { useEffect, useState } from 'react'

import type { ScheduledTask, ScheduleSpec, ScheduleType } from '../../services/scheduled'
import { useAppStore } from '../../stores/useAppStore'
import { useTaskStore } from '../../stores/useTaskStore'
import { toast } from '../../utils/toast'
import { WEEKDAYS, WEEKDAY_LABEL } from '../../utils/schedule'
import Icon from '../Icon'

interface Props {
  open: boolean
  task?: ScheduledTask | null
  onClose: () => void
}

const TYPES: { value: ScheduleType; label: string }[] = [
  { value: 'minute', label: '每隔 N 分钟' },
  { value: 'hour', label: '每小时' },
  { value: 'day', label: '每天' },
  { value: 'week', label: '每周' },
  { value: 'month', label: '每月' },
]

const HOURS = Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0'))
const MINUTES = Array.from({ length: 60 }, (_, i) => String(i).padStart(2, '0'))
// 后端 spec_to_cron 允许每月 1-31 日
const DAYS = Array.from({ length: 31 }, (_, i) => String(i + 1))
/** 后端限制 everyMinutes 为 1-59 */
const MIN_EVERY = 1
const MAX_EVERY = 59

const pad2 = (value: number | string) => String(value).padStart(2, '0')

/**
 * 新建 / 编辑自动化任务：字段与后端 ScheduleSpec 完全对应。
 */
export default function AutoTaskForm({ open, task, onClose }: Props) {
  const workspaces = useAppStore((s) => s.workspaces)
  const defaultWorkspaceId = useAppStore((s) => s.workspaceId)
  const openDialog = useAppStore((s) => s.openDialog)
  const createTask = useTaskStore((s) => s.create)
  const updateTask = useTaskStore((s) => s.update)

  const [name, setName] = useState('')
  const [instruction, setInstruction] = useState('')
  const [workspaceId, setWorkspaceId] = useState(defaultWorkspaceId)
  const [type, setType] = useState<ScheduleType>('day')
  const [everyMinutes, setEveryMinutes] = useState('30')
  const [hour, setHour] = useState('09')
  const [minute, setMinute] = useState('00')
  const [weekday, setWeekday] = useState('mon')
  const [day, setDay] = useState('1')
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!open) return
    setName(task?.name ?? '')
    setInstruction(task?.instruction ?? '')
    setWorkspaceId(task?.workspace_id || defaultWorkspaceId)
    const spec = task?.schedule
    const specType = spec && TYPES.some((item) => item.value === spec.type) ? spec.type : 'day'
    setType(specType as ScheduleType)
    setEveryMinutes(spec?.type === 'minute' ? String(spec.everyMinutes) : '30')
    setHour(spec && 'hour' in spec ? pad2(spec.hour) : '09')
    setMinute(spec && 'minute' in spec ? pad2(spec.minute) : '00')
    setWeekday(spec?.type === 'week' ? spec.weekday : 'mon')
    setDay(spec?.type === 'month' ? String(spec.day) : '1')
    setDirty(false)
  }, [open, task, defaultWorkspaceId])

  useEffect(() => {
    if (!open || !workspaces.length) return
    // 工作区列表晚于表单加载完成时，修正无效的默认值
    setWorkspaceId((current) => {
      if (workspaces.some((workspace) => workspace.id === current)) return current
      const fromTask = task?.workspace_id
      if (fromTask && workspaces.some((workspace) => workspace.id === fromTask)) return fromTask
      return workspaces[0].id
    })
  }, [open, workspaces, task])

  if (!open) return null

  const workspaceIndex = Math.max(
    0,
    workspaces.findIndex((workspace) => workspace.id === workspaceId),
  )
  const typeIndex = Math.max(0, TYPES.findIndex((item) => item.value === type))
  /** raw（Cron）任务无法用表单字段表达，编辑时保持原表达式不覆盖 */
  const rawCron =
    task && task.schedule.type === 'raw' ? task.schedule.cron : ''

  const buildSchedule = (): ScheduleSpec => {
    if (type === 'minute') {
      const every = Math.min(MAX_EVERY, Math.max(MIN_EVERY, Number(everyMinutes) || MIN_EVERY))
      return { type: 'minute', everyMinutes: every }
    }
    if (type === 'hour') return { type: 'hour', minute: Number(minute) }
    if (type === 'week') {
      return { type: 'week', weekday, hour: Number(hour), minute: Number(minute) }
    }
    if (type === 'month') {
      return { type: 'month', day: Number(day), hour: Number(hour), minute: Number(minute) }
    }
    return { type: 'day', hour: Number(hour), minute: Number(minute) }
  }

  const canConfirm =
    !!name.trim() &&
    !!instruction.trim() &&
    workspaces.some((workspace) => workspace.id === workspaceId) &&
    !saving

  const submit = async () => {
    if (!canConfirm) return
    setSaving(true)
    try {
      if (task) {
        await updateTask(task.id, {
          name: name.trim(),
          instruction: instruction.trim(),
          workspace_id: workspaceId,
          ...(rawCron ? {} : { schedule: buildSchedule() }),
        })
        toast('已保存')
      } else {
        await createTask({
          name: name.trim(),
          instruction: instruction.trim(),
          workspace_id: workspaceId,
          schedule: buildSchedule(),
        })
        toast('自动化已创建')
      }
      onClose()
    } catch (error) {
      toast((error as Error).message)
    } finally {
      setSaving(false)
    }
  }

  /** 有未保存内容时先确认，避免误点遮罩/关闭导致输入丢失 */
  const requestClose = () => {
    if (saving) return
    if (!dirty) {
      onClose()
      return
    }
    openDialog({
      kind: 'confirm',
      title: '放弃未保存的修改？',
      desc: '表单内容还没有保存，离开后不会保留。',
      confirmText: '放弃修改',
      cancelText: '继续编辑',
      onConfirm: () => {
        setDirty(false)
        onClose()
      },
    })
  }

  const hourPicker = (
    <Picker
      className='tf-picker'
      mode='selector'
      range={HOURS}
      value={HOURS.indexOf(hour)}
      onChange={(event) => {
        setHour(HOURS[Number(event.detail.value)])
        setDirty(true)
      }}
    >
      <View className='tf-input tf-select tf-time'>
        <Text>{hour}</Text>
        <Icon name='chevron-down' size={15} color='#8B95A8' strokeWidth={1.8} />
      </View>
    </Picker>
  )

  const minutePicker = (
    <Picker
      className='tf-picker'
      mode='selector'
      range={MINUTES}
      value={MINUTES.indexOf(minute)}
      onChange={(event) => {
        setMinute(MINUTES[Number(event.detail.value)])
        setDirty(true)
      }}
    >
      <View className='tf-input tf-select tf-time'>
        <Text>{minute}</Text>
        <Icon name='chevron-down' size={15} color='#8B95A8' strokeWidth={1.8} />
      </View>
    </Picker>
  )

  const dayPicker = (
    <Picker
      className='tf-picker'
      mode='selector'
      range={DAYS}
      value={Math.max(0, DAYS.indexOf(day))}
      onChange={(event) => {
        setDay(DAYS[Number(event.detail.value)])
        setDirty(true)
      }}
    >
      <View className='tf-input tf-select tf-time'>
        <Text>{day}</Text>
        <Icon name='chevron-down' size={15} color='#8B95A8' strokeWidth={1.8} />
      </View>
    </Picker>
  )

  const timePickers = (
    <View className='tf-row'>
      {hourPicker}
      <Text className='tf-unit'>:</Text>
      {minutePicker}
    </View>
  )

  return (
    <View className='tf-layer' onClick={requestClose}>
      <View className='tf-sheet' onClick={(event) => event.stopPropagation()}>
        <View className='tf-head'>
          <View className='tf-head-text'>
            <Text className='tf-title'>{task ? '编辑自动化' : '创建新任务'}</Text>
            <Text className='tf-sub'>填写基本信息、选择工作区，再设定执行周期</Text>
          </View>
          <View className='tf-close' onClick={requestClose}>
            <Icon name='x' size={20} color='#575B62' strokeWidth={1.8} />
          </View>
        </View>

        <ScrollView scrollY className='tf-body' style={{ height: 0, flex: 1, minHeight: 0 }}>
          <View className='tf-field'>
            <View className='tf-label'>
              <Text className='tf-name'>任务名称</Text>
              <Text className='tf-req'>*</Text>
              <Text className='tf-count'>{name.length}/50</Text>
            </View>
            <Input
              className='tf-input'
              value={name}
              maxlength={50}
              placeholder='请输入'
              onInput={(event) => {
                setName(event.detail.value)
                setDirty(true)
              }}
            />
          </View>

          <View className='tf-field'>
            <View className='tf-label'>
              <Text className='tf-name'>任务指令</Text>
              <Text className='tf-req'>*</Text>
              <Text className='tf-count'>{instruction.length}/3000</Text>
            </View>
            <Textarea
              className='tf-input tf-textarea'
              value={instruction}
              maxlength={3000}
              placeholder='描述每次自动执行时要完成的事情'
              onInput={(event) => {
                setInstruction(event.detail.value)
                setDirty(true)
              }}
            />
          </View>

          <View className='tf-field'>
            <View className='tf-label'>
              <Text className='tf-name'>执行工作区</Text>
            </View>
            <Picker
              mode='selector'
              range={workspaces.map((workspace) => workspace.name)}
              value={workspaceIndex}
              onChange={(event) => {
                const picked = workspaces[Number(event.detail.value)]
                if (picked) {
                  setWorkspaceId(picked.id)
                  setDirty(true)
                }
              }}
            >
              <View className='tf-input tf-select'>
                <Text>{workspaces[workspaceIndex]?.name ?? '请选择'}</Text>
                <Icon name='chevron-down' size={16} color='#8B95A8' strokeWidth={1.8} />
              </View>
            </Picker>
            {!workspaces.length ? (
              <Text className='tf-warn'>还没有同步到工作区，请检查服务器连接后重试。</Text>
            ) : null}
          </View>

          <View className='tf-field'>
            <View className='tf-label'>
              <Text className='tf-name'>执行周期</Text>
              {rawCron ? null : <Text className='tf-req'>*</Text>}
            </View>
            {rawCron ? (
              <>
                <View className='tf-input tf-readonly'>
                  <Text>{rawCron}</Text>
                </View>
                <Text className='tf-warn'>
                  该任务使用 Cron 表达式，移动端暂不支持修改周期；保存时会保留原表达式。
                </Text>
              </>
            ) : (
              <>
                <Picker
                  mode='selector'
                  range={TYPES.map((item) => item.label)}
                  value={typeIndex}
                  onChange={(event) => {
                    setType(TYPES[Number(event.detail.value)].value)
                    setDirty(true)
                  }}
                >
                  <View className='tf-input tf-select'>
                    <Text>{TYPES[typeIndex].label}</Text>
                    <Icon name='chevron-down' size={16} color='#8B95A8' strokeWidth={1.8} />
                  </View>
                </Picker>

                {type === 'minute' ? (
                  <View className='tf-row'>
                    <Text className='tf-unit'>每</Text>
                    <Input
                      className='tf-input tf-num'
                      type='number'
                      value={everyMinutes}
                      onInput={(event) => {
                        setEveryMinutes(event.detail.value.replace(/[^\d]/g, ''))
                        setDirty(true)
                      }}
                      onBlur={() =>
                        setEveryMinutes(
                          String(
                            Math.min(
                              MAX_EVERY,
                              Math.max(MIN_EVERY, Number(everyMinutes) || MIN_EVERY),
                            ),
                          ),
                        )
                      }
                    />
                    <Text className='tf-unit'>分钟执行（1-59）</Text>
                  </View>
                ) : null}

                {type === 'hour' ? (
                  <View className='tf-row'>
                    <Text className='tf-unit'>每小时第</Text>
                    {minutePicker}
                    <Text className='tf-unit'>分执行</Text>
                  </View>
                ) : null}

                {type === 'day' ? timePickers : null}

                {type === 'week' ? (
                  <View className='tf-row'>
                    <Text className='tf-unit'>每周</Text>
                    <Picker
                      mode='selector'
                      range={WEEKDAYS.map((value) => WEEKDAY_LABEL[value])}
                      value={WEEKDAYS.indexOf(weekday as (typeof WEEKDAYS)[number])}
                      onChange={(event) => {
                        setWeekday(WEEKDAYS[Number(event.detail.value)])
                        setDirty(true)
                      }}
                    >
                      <View className='tf-input tf-select tf-time'>
                        <Text>{WEEKDAY_LABEL[weekday]}</Text>
                        <Icon name='chevron-down' size={15} color='#8B95A8' strokeWidth={1.8} />
                      </View>
                    </Picker>
                    <Text className='tf-unit'>·</Text>
                    {hourPicker}
                    <Text className='tf-unit'>:</Text>
                    {minutePicker}
                  </View>
                ) : null}

                {type === 'month' ? (
                  <View className='tf-row'>
                    <Text className='tf-unit'>每月</Text>
                    {dayPicker}
                    <Text className='tf-unit'>日</Text>
                    {hourPicker}
                    <Text className='tf-unit'>:</Text>
                    {minutePicker}
                  </View>
                ) : null}
              </>
            )}
          </View>
        </ScrollView>

        <View className='tf-foot'>
          <Text className='tf-note'>{task ? '保存后按设定的周期自动执行' : '创建后按设定的周期自动执行'}</Text>
          <View className='tf-actions'>
            <View className='tf-btn' onClick={requestClose}>
              <Text>取消</Text>
            </View>
            <View
              className={`tf-btn is-primary${canConfirm ? '' : ' is-disabled'}`}
              onClick={() => void submit()}
            >
              <Text>{saving ? '保存中…' : '确认'}</Text>
            </View>
          </View>
        </View>
      </View>
    </View>
  )
}
