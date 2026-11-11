import { ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'

import AppDialog from '../../components/AppDialog'
import AppHeader from '../../components/AppHeader'
import AutoTaskForm from '../../components/AutoTaskForm'
import EmptyState from '../../components/EmptyState'
import Icon from '../../components/Icon'
import { useThemeClass } from '../../hooks/useThemeClass'
import type { ScheduledTask } from '../../services/scheduled'
import { useAppStore } from '../../stores/useAppStore'
import { useTaskStore } from '../../stores/useTaskStore'
import { scheduleLabel } from '../../utils/schedule'
import { formatRelative } from '../../utils/format'
import { goBack, toast } from '../../utils/toast'

export default function Automations() {
  const tasks = useTaskStore((s) => s.tasks)
  const tasksLoading = useTaskStore((s) => s.loading)
  const loadTasks = useTaskStore((s) => s.load)
  const toggleTask = useTaskStore((s) => s.toggle)
  const triggerTask = useTaskStore((s) => s.trigger)
  const removeTask = useTaskStore((s) => s.remove)
  const openDialog = useAppStore((s) => s.openDialog)
  const loadWorkspaces = useAppStore((s) => s.loadWorkspaces)

  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<ScheduledTask | null>(null)

  useEffect(() => {
    loadWorkspaces().catch(() => undefined)
    loadTasks('all').catch((error: Error) => toast(error.message))
  }, [])

  const openTask = (task: ScheduledTask) => {
    Taro.navigateTo({ url: `/pages/scheduled/index?id=${task.id}` })
  }

  const openActions = (task: ScheduledTask) => {
    openDialog({
      kind: 'sheet',
      title: task.name,
      items: [
        { label: '立即运行', value: 'run' },
        { label: '查看运行记录', value: 'open' },
        { label: '编辑自动化', value: 'edit' },
        { label: task.enabled ? '暂停自动化' : '启用自动化', value: 'toggle' },
        { label: '删除自动化', value: 'delete', danger: true },
      ],
      onSelect: (value) => {
        if (value === 'run') {
          triggerTask(task.id)
            .then(() => toast('已触发，可在运行记录查看'))
            .catch((error: Error) => toast(error.message))
        } else if (value === 'open') {
          openTask(task)
        } else if (value === 'edit') {
          setEditing(task)
          setFormOpen(true)
        } else if (value === 'toggle') {
          toggleTask(task.id, !task.enabled)
            .then(() => toast(task.enabled ? '已暂停' : '已启用'))
            .catch((error: Error) => toast(error.message))
        } else if (value === 'delete') {
          openDialog({
            kind: 'danger',
            title: `删除「${task.name}」？`,
            desc: '删除后该自动化的调度与运行记录将无法恢复，已生成的产出物会保留在服务器。',
            confirmText: '删除自动化',
            onConfirm: () => {
              removeTask(task.id)
                .then(() => toast('自动化已删除'))
                .catch((error: Error) => toast(error.message))
            },
          })
        }
      },
    })
  }

  const createTask = () => {
    setEditing(null)
    setFormOpen(true)
  }

  const refreshTasks = () => {
    loadTasks('all')
      .then(() => toast('已刷新'))
      .catch((error: Error) => toast(error.message))
  }

  return (
    <View className={useThemeClass('page')}>
      <AppHeader
        variant='automations'
        title='自动化'
        onBack={() => goBack('/pages/tasks/index')}
        onNew={createTask}
        onRefresh={refreshTasks}
      />

      <ScrollView scrollY className='app-body' style={{ height: 0, flex: 1, minHeight: 0 }}>
        <View className='auto-list'>
          {!tasks.length ? (
            tasksLoading ? (
              <EmptyState title='正在加载自动化…' desc='从服务器同步任务列表' />
            ) : (
              <EmptyState
                title='还没有自动化'
                desc={'把重复的整理、汇总、比对交给服务器按周期执行\n创建后会在运行记录中保留每次结果'}
              >
                <View className='empty-action' onClick={createTask}>
                  <Icon name='plus' size={15} color='#FFFFFF' strokeWidth={2} />
                  <Text>新建自动化</Text>
                </View>
              </EmptyState>
            )
          ) : null}
          {tasks.map((task) => (
            <View className='auto-card reveal' key={task.id} onClick={() => openTask(task)}>
              <View className='auto-head'>
                <Text className='auto-title'>{task.name}</Text>
                {task.last_status === 'failed' ? <Text className='auto-tag is-error'>失败</Text> : null}
                <View
                  className={`auto-switch${task.enabled ? ' is-on' : ''}`}
                  onClick={(event) => {
                    event.stopPropagation()
                    toggleTask(task.id, !task.enabled)
                      .then(() => toast(task.enabled ? '已暂停' : '已启用'))
                      .catch((error: Error) => toast(error.message))
                  }}
                >
                  <View className={`auto-knob${task.enabled ? ' is-on' : ''}`} />
                </View>
              </View>

              <Text className='auto-desc'>{task.instruction}</Text>

              <View className='auto-rule' />

              <View className='auto-foot'>
                <Icon name='clock' size={13} color='#9AA0A8' />
                <Text className='auto-schedule'>{scheduleLabel(task.schedule)}</Text>
                {task.last_fired_at ? (
                  <Text className='auto-schedule'>
                    · 上次 {formatRelative(task.last_fired_at)}
                  </Text>
                ) : null}
                <View
                  className='auto-more'
                  onClick={(event) => {
                    event.stopPropagation()
                    openActions(task)
                  }}
                >
                  <Icon name='more-horizontal' size={18} color='#9AA0A8' />
                </View>
                <Icon name='chevron-right' size={15} color='#9AA0A8' />
              </View>
            </View>
          ))}
        </View>
      </ScrollView>

      <AutoTaskForm
        open={formOpen}
        task={editing}
        onClose={() => {
          setFormOpen(false)
          setEditing(null)
        }}
      />

      <AppDialog />
    </View>
  )
}
