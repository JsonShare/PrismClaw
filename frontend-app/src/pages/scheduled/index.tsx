import { ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useRef, useState } from 'react'

import AppDialog from '../../components/AppDialog'
import AppHeader from '../../components/AppHeader'
import AutoTaskForm from '../../components/AutoTaskForm'
import EmptyState from '../../components/EmptyState'
import Icon from '../../components/Icon'
import { useThemeClass } from '../../hooks/useThemeClass'
import { useAppStore } from '../../stores/useAppStore'
import { useTaskStore } from '../../stores/useTaskStore'
import { formatDuration, formatRelative, statusText } from '../../utils/format'
import { scheduleLabel } from '../../utils/schedule'
import { goBack, toast } from '../../utils/toast'

export default function Scheduled() {
  const taskId = Taro.getCurrentInstance().router?.params?.id || ''
  const tasks = useTaskStore((s) => s.tasks)
  const tasksLoading = useTaskStore((s) => s.loading)
  const runs = useTaskStore((s) => s.runs[taskId])
  const runsLoading = useTaskStore((s) => !!s.runsLoading[taskId])
  const loadTasks = useTaskStore((s) => s.load)
  const loadRuns = useTaskStore((s) => s.loadRuns)
  const toggleTask = useTaskStore((s) => s.toggle)
  const triggerTask = useTaskStore((s) => s.trigger)
  const removeTask = useTaskStore((s) => s.remove)
  const openDialog = useAppStore((s) => s.openDialog)
  const [formOpen, setFormOpen] = useState(false)
  const [runsError, setRunsError] = useState('')
  const [listError, setListError] = useState('')
  const timers = useRef<ReturnType<typeof setTimeout>[]>([])

  const task = tasks.find((item) => item.id === taskId) || null

  useEffect(() => {
    return () => {
      timers.current.forEach(clearTimeout)
      timers.current = []
    }
  }, [])

  const refreshRuns = () => {
    if (!taskId) return
    loadRuns(taskId)
      .then(() => setRunsError(''))
      .catch((error: Error) => {
        setRunsError(error.message)
        toast(error.message)
      })
  }

  const retryTasks = () => {
    setListError('')
    loadTasks('all').catch((error: Error) => setListError(error.message))
  }

  useEffect(() => {
    setListError('')
    if (!tasks.length) {
      loadTasks('all').catch((error: Error) => setListError(error.message))
    }
    refreshRuns()
  }, [taskId])

  if (!task) {
    return (
      <View className={useThemeClass('page')}>
        <AppHeader
          variant='scheduled'
          title='自动化任务'
          sub='Work · 服务器调度'
          onBack={() => goBack('/pages/automations/index')}
        />
        <View className='app-body'>
          {tasksLoading ? (
            <EmptyState title='正在加载任务…' desc='正在从服务器同步自动化配置' />
          ) : listError ? (
            <EmptyState title='任务加载失败' desc={listError}>
              <View className='empty-action' onClick={retryTasks}>
                <Text>重新加载</Text>
              </View>
            </EmptyState>
          ) : (
            <EmptyState title='任务不存在' desc='请从自动化列表重新选择' />
          )}
        </View>
      </View>
    )
  }

  const taskRuns = runs ?? []

  const confirmDelete = () => {
    openDialog({
      kind: 'danger',
      title: `删除「${task.name}」？`,
      desc: '删除后该自动化的调度与运行记录将无法恢复，已生成的产出物会保留在服务器。',
      confirmText: '删除自动化',
      onConfirm: () => {
        removeTask(task.id)
          .then(() => {
            toast('自动化已删除')
            goBack('/pages/automations/index')
          })
          .catch((error: Error) => toast(error.message))
      },
    })
  }

  return (
    <View className={useThemeClass('page')}>
      <AppHeader
        variant='scheduled'
        title={task.name}
        sub='Work · 服务器调度'
        onBack={() => goBack('/pages/automations/index')}
      />

      <ScrollView scrollY className='app-body' style={{ height: 0, flex: 1, minHeight: 0 }}>
        <View className='page-pad' style={{ paddingTop: '2px' }}>
          <View className='task-card reveal'>
            <View className='task-top'>
              <View style={{ flex: 1, minWidth: 0 }}>
                <Text className='task-name'>{task.name}</Text>
                <Text className='task-desc'>{task.instruction}</Text>
              </View>
              <Text className={`task-badge ${task.enabled ? 'is-on' : 'is-off'}`}>
                {task.enabled ? '已启用' : '已暂停'}
              </Text>
            </View>
            <View className='task-foot'>
              <Icon name='clock' size={13} color='#9AA0A8' />
              <Text>{scheduleLabel(task.schedule)}</Text>
              <Text>
                {task.last_fired_at ? `· 上次 ${formatRelative(task.last_fired_at)}` : '· 尚未运行'}
              </Text>
            </View>
            <View className='task-detail-actions'>
              <View
                className='mini-btn'
                onClick={() => {
                  triggerTask(task.id)
                    .then(() => {
                      toast('已触发，正在执行')
                      // 运行创建有延迟，稍后再刷新两次以拿到最新状态
                      timers.current.push(
                        setTimeout(refreshRuns, 2000),
                        setTimeout(refreshRuns, 6000),
                      )
                    })
                    .catch((error: Error) => toast(error.message))
                }}
              >
                <Icon name='play' size={12} color='#676D78' />
                <Text>立即运行</Text>
              </View>
              <View
                className='mini-btn'
                onClick={() => {
                  toggleTask(task.id, !task.enabled)
                    .then(() => toast(task.enabled ? '已暂停' : '已启用'))
                    .catch((error: Error) => toast(error.message))
                }}
              >
                <Text>{task.enabled ? '暂停任务' : '启用任务'}</Text>
              </View>
              <View className='mini-btn' onClick={() => setFormOpen(true)}>
                <Text>编辑</Text>
              </View>
              <View className='mini-btn is-danger' onClick={confirmDelete}>
                <Text>删除</Text>
              </View>
            </View>
          </View>

          <View className='section-label'>
            <Text>运行记录</Text>
            <View className='section-label-right' onClick={refreshRuns}>
              <Text>{runsLoading ? '同步中…' : `${taskRuns.length} 次`}</Text>
              <Icon name='refresh' size={13} color='#9AA0A8' />
            </View>
          </View>

          {runsError && !taskRuns.length ? (
            <EmptyState title='运行记录加载失败' desc={runsError}>
              <View className='empty-action' onClick={refreshRuns}>
                <Text>重新加载</Text>
              </View>
            </EmptyState>
          ) : taskRuns.length ? (
            <View className='run-timeline'>
              {taskRuns.map((run) => {
                const live = ['running', 'executing', 'pending'].includes(run.status)
                const ok = run.status === 'success'
                const stateClass = ok ? 'is-success' : live ? 'is-live' : 'is-warn'
                return (
                  <View className='tl-item' key={run.id}>
                    <View
                      className='tl-card'
                      onClick={() => {
                        if (!run.session_id) {
                          toast(run.error || '该次运行没有生成会话')
                          return
                        }
                        Taro.navigateTo({ url: `/pages/chat/index?id=${run.session_id}` })
                      }}
                    >
                      <View className='tl-head'>
                        <View className={`tl-ico ${stateClass}`}>
                          <Icon
                            name={ok ? 'check-circle' : live ? 'clock' : 'alert-triangle'}
                            size={18}
                            color='#575B62'
                          />
                        </View>
                        <View className='tl-main'>
                          <View className='tl-top'>
                            <Text className='tl-time'>{formatRelative(run.fired_at)}</Text>
                            <Text className={`tl-badge ${stateClass}`}>
                              {statusText(run.status)}
                            </Text>
                          </View>
                          <Text className='tl-summary'>
                            {run.error ||
                              (live
                                ? '正在服务器执行，稍后刷新查看结果'
                                : run.session_id
                                  ? '已生成执行会话，点击查看详情'
                                  : '等待执行')}
                          </Text>
                        </View>
                      </View>
                      <View className='tl-rule' />
                      <View className='tl-foot'>
                        <Icon name='clock' size={13} color='#9AA0A8' />
                        <Text className='tl-duration'>
                          {run.duration_s != null
                            ? `耗时 ${formatDuration(run.duration_s)}`
                            : live
                              ? '执行中'
                              : '—'}
                        </Text>
                        {run.session_id ? (
                          <View className='tl-link'>
                            <Text>查看会话</Text>
                          </View>
                        ) : null}
                      </View>
                    </View>
                  </View>
                )
              })}
            </View>
          ) : runsLoading ? (
            <View className='at-empty'>
              <Text>正在加载运行记录…</Text>
            </View>
          ) : (
            <EmptyState title='暂无运行记录' desc='任务触发后会在这里生成结果' />
          )}

          <Text className='detail-note'>该任务由服务器端调度执行，与 Chat 会话共用同一份任务定义。</Text>
        </View>
      </ScrollView>

      <AutoTaskForm open={formOpen} task={task} onClose={() => setFormOpen(false)} />
      <AppDialog />
    </View>
  )
}
