import { ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useMemo, useState } from 'react'

import AppDialog from '../../components/AppDialog'
import Icon from '../../components/Icon'
import { useThemeClass } from '../../hooks/useThemeClass'
import { deleteSession, renameSession } from '../../services/sessions'
import type { ScheduledTask } from '../../services/scheduled'
import { useAppStore } from '../../stores/useAppStore'
import { useAuthStore } from '../../stores/useAuthStore'
import { useChatStore } from '../../stores/useChatStore'
import { useTaskStore } from '../../stores/useTaskStore'
import { formatRelative, toEpochMs } from '../../utils/format'
import { toast } from '../../utils/toast'

const SCOPE_ALL = 'all'

interface Row {
  key: string
  kind: 'chat' | 'scheduled'
  title: string
  meta: string
  timeMs: number
  task?: ScheduledTask
}

function periodOf(timeMs: number) {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime()
  const weekStart = today - ((now.getDay() + 6) % 7) * 86400000
  if (timeMs >= weekStart) return '本周'
  const monthStart = new Date(now.getFullYear(), now.getMonth(), 1).getTime()
  return timeMs >= monthStart ? '本月' : '更早'
}

export default function Tasks() {
  const conversations = useChatStore((s) => s.conversations)
  const listLoading = useChatStore((s) => s.listLoading)
  const listHasMore = useChatStore((s) => s.listHasMore)
  const loadConversations = useChatStore((s) => s.loadConversations)
  const removeConversation = useChatStore((s) => s.removeConversation)
  const renameConversation = useChatStore((s) => s.renameConversation)

  const tasks = useTaskStore((s) => s.tasks)
  const tasksLoading = useTaskStore((s) => s.loading)
  const loadTasks = useTaskStore((s) => s.load)
  const toggleTask = useTaskStore((s) => s.toggle)
  const triggerTask = useTaskStore((s) => s.trigger)
  const removeTask = useTaskStore((s) => s.remove)

  const openDialog = useAppStore((s) => s.openDialog)
  const nickname = useAuthStore((s) => s.nickname)
  const userId = useAuthStore((s) => s.userId)
  const displayName = nickname || userId || '未登录'

  const [sorter, setSorter] = useState<'recent' | 'name'>('recent')
  const [listError, setListError] = useState('')
  const [open, setOpen] = useState(true)
  const [closing, setClosing] = useState(false)

  useEffect(() => {
    setListError('')
    loadConversations(SCOPE_ALL, true).catch((error: Error) => setListError(error.message))
    loadTasks(SCOPE_ALL).catch((error: Error) => toast(error.message))
  }, [loadConversations, loadTasks])

  const retryList = () => {
    setListError('')
    loadConversations(SCOPE_ALL, true).catch((error: Error) => setListError(error.message))
  }

  const loadMore = () => {
    // 直接读 store：滚动事件可能在同一帧内连续触发，闭包里的 listLoading 会滞后
    const state = useChatStore.getState()
    if (state.listLoading || !state.listHasMore) return
    loadConversations(SCOPE_ALL, false).catch((error: Error) => toast(error.message))
  }

  const rows = useMemo(() => {
    const list: Row[] = []
    conversations.forEach((conv) => {
      list.push({
        key: conv.id,
        kind: 'chat',
        title: conv.title?.trim() || '未命名会话',
        meta: (conv.message_count ?? 0) > 0 ? `${conv.message_count} 条消息` : '新会话',
        timeMs: conv.last_interaction_at ? new Date(conv.last_interaction_at).getTime() : conv.created_at ? new Date(conv.created_at).getTime() : 0,
      })
    })
    tasks.forEach((task) => {
      list.push({
        key: task.id,
        kind: 'scheduled',
        title: task.name,
        meta: task.enabled ? '自动化 · 已启用' : '自动化 · 已暂停',
        // last_fired_at 为秒级时间戳，需与毫秒时间统一后再参与排序
        timeMs: toEpochMs(task.last_fired_at) || toEpochMs(task.created_at),
        task,
      })
    })
    return sorter === 'name'
      ? list.slice().sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN'))
      : list.slice().sort((a, b) => b.timeMs - a.timeMs)
  }, [conversations, tasks, sorter])

  const rowGroups = useMemo(() => {
    if (sorter === 'name') return rows.length ? [{ label: '历史任务', items: rows }] : []
    return ['本周', '本月', '更早']
      .map((label) => ({ label, items: rows.filter((row) => periodOf(row.timeMs) === label) }))
      .filter((group) => group.items.length > 0)
  }, [rows, sorter])

  const openRow = (row: Row) => {
    if (row.kind === 'scheduled') {
      Taro.navigateTo({ url: `/pages/scheduled/index?id=${row.key}` })
      return
    }
    Taro.navigateTo({ url: `/pages/chat/index?id=${row.key}` })
  }

  const deleteChat = (row: Row) => {
    deleteSession(row.key)
      .then(() => {
        removeConversation(row.key)
        toast('会话已删除')
      })
      .catch((error: Error) => toast(error.message))
  }

  const renameChat = (row: Row) => {
    openDialog({
      kind: 'form',
      title: '重命名会话',
      confirmText: '保存',
      fields: [{ key: 'name', label: '会话名称', value: row.title }],
      onConfirm: (values) => {
        const name = values.name.trim()
        if (!name) return
        renameSession(row.key, name)
          .then(() => {
            renameConversation(row.key, name)
            toast('已重命名')
          })
          .catch((error: Error) => toast(error.message))
      },
    })
  }

  const openRowActions = (row: Row) => {
    if (row.kind === 'scheduled' && row.task) {
      const task = row.task
      openDialog({
        kind: 'sheet',
        title: row.title,
        items: [
          { label: '查看运行记录', value: 'open' },
          { label: task.enabled ? '暂停自动化' : '启用自动化', value: 'toggle' },
          { label: '立即运行', value: 'run' },
          { label: '删除自动化', value: 'delete', danger: true },
        ],
        onSelect: (value) => {
          if (value === 'open') openRow(row)
          if (value === 'toggle') {
            toggleTask(task.id, !task.enabled)
              .then(() => toast(task.enabled ? '已暂停' : '已启用'))
              .catch((error: Error) => toast(error.message))
          }
          if (value === 'run') {
            triggerTask(task.id).then(() => toast('已触发，可在运行记录查看')).catch((error: Error) => toast(error.message))
          }
          if (value === 'delete') {
            openDialog({
              kind: 'danger',
              title: `删除「${task.name}」？`,
              desc: '调度与运行记录将删除，已生成的产出物会保留在服务器。',
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
      return
    }
    openDialog({
      kind: 'sheet',
      title: row.title,
      items: [
        { label: '打开会话', value: 'open' },
        { label: '重命名会话', value: 'rename' },
        { label: '删除会话', value: 'delete', danger: true },
      ],
      onSelect: (value) => {
        if (value === 'open') openRow(row)
        if (value === 'rename') renameChat(row)
        if (value === 'delete') {
          openDialog({
            kind: 'danger',
            title: `删除「${row.title}」？`,
            desc: '会话消息与执行记录会一并删除，且无法恢复。',
            confirmText: '删除会话',
            onConfirm: () => deleteChat(row),
          })
        }
      },
    })
  }

  const pickSorter = () => {
    openDialog({
      kind: 'sheet',
      title: '列表排序',
      items: [
        { label: '按时间', value: 'recent', active: sorter === 'recent' },
        { label: '按名称', value: 'name', active: sorter === 'name' },
      ],
      onSelect: (value) => setSorter(value === 'name' ? 'name' : 'recent'),
    })
  }

  const goHome = () => {
    if (Taro.getCurrentPages().length > 1) Taro.navigateBack()
    else Taro.reLaunch({ url: '/pages/home/index' })
  }

  const closeDrawer = (action: () => void) => {
    if (closing) return
    setClosing(true)
    window.setTimeout(action, 280)
  }

  return (
    <View className={`${useThemeClass('page at-drawer-page')}${open && !closing ? ' is-open' : ''}`}>
      <View className='at-drawer-mask' onClick={() => closeDrawer(goHome)} />
      <View className='at-drawer-panel'>
      <View className='at-entries'>
        <View className='at-entry' onClick={() => closeDrawer(goHome)}>
          <View className='at-entry-icon'>
            <Icon name='message-plus' size={22} color='#1F232B' strokeWidth={1.8} />
          </View>
          <Text>新任务</Text>
        </View>
        <View className='at-entry' onClick={() => closeDrawer(() => Taro.navigateTo({ url: '/pages/automations/index' }))}>
          <View className='at-entry-icon'>
            <Icon name='clock' size={22} color='#1F232B' strokeWidth={1.8} />
          </View>
          <Text>自动化</Text>
        </View>
        <View className='at-entry' onClick={() => closeDrawer(() => Taro.navigateTo({ url: '/pages/files/index' }))}>
          <View className='at-entry-icon'>
            <Icon name='folder' size={22} color='#1F232B' strokeWidth={1.8} />
          </View>
          <Text>我的文件</Text>
        </View>
      </View>

      <View className='at-rule' />

      <View className='at-section'>
        <Text className='at-section-title'>任务历史</Text>
        <View className='at-sort' onClick={pickSorter}>
          <Text>{sorter === 'name' ? '按名称' : '按时间'}</Text>
          <Icon name='chevron-down' size={13} color='#8E8E93' strokeWidth={1.8} />
        </View>
      </View>

      <ScrollView
        scrollY
        className='at-scroll'
        style={{ height: 0, flex: 1, minHeight: 0 }}
        lowerThreshold={120}
        onScrollToLower={loadMore}
      >
        {rowGroups.length ? (
          rowGroups.map((group) => (
            <View className='at-group' key={group.label}>
              <Text className='at-group-title'>{group.label}</Text>
              {group.items.map((row) => (
                <View
                  className='at-row'
                  key={row.key}
                  onClick={() => closeDrawer(() => openRow(row))}
                  onLongPress={() => openRowActions(row)}
                >
                  <View className='at-chip'>
                    <Icon
                      name={row.kind === 'scheduled' ? 'clock' : 'message'}
                      size={19}
                      color='#1F232B'
                      strokeWidth={1.7}
                    />
                    {row.kind === 'scheduled' && row.task?.enabled ? <View className='at-status-dot' /> : null}
                  </View>
                  <View className='at-main'>
                    <Text className='at-name'>{row.title}</Text>
                    <View className='at-meta'>
                      <Text>{row.meta}</Text>
                    </View>
                  </View>
                  <View className='at-side'>
                    <Text className='at-time'>{formatRelative(row.timeMs)}</Text>
                    <View
                      className='at-more'
                      onClick={(event) => {
                        event.stopPropagation()
                        openRowActions(row)
                      }}
                    >
                      <Icon name='more-horizontal' size={17} color='#9A9AA0' strokeWidth={1.8} />
                    </View>
                  </View>
                </View>
              ))}
            </View>
          ))
        ) : (
          <View className='at-empty'>
            {listError ? (
              <>
                <Text>任务列表加载失败</Text>
                <View className='empty-action' onClick={retryList}>
                  <Text>重新加载</Text>
                </View>
              </>
            ) : (
              <Text>
                {listLoading || tasksLoading ? '加载中…' : '还没有历史任务，点击「新任务」开始'}
              </Text>
            )}
          </View>
        )}
        {listHasMore ? (
          <View className={`at-loadmore${listLoading ? ' is-loading' : ''}`}>
            <Text>{listLoading ? '正在加载…' : '上滑加载更早的会话'}</Text>
          </View>
        ) : null}
      </ScrollView>

      <View className='at-profile' onClick={() => closeDrawer(() => Taro.navigateTo({ url: '/pages/me/index' }))}>
        <View className='at-avatar'>
          <Text>{displayName.trim().charAt(0).toUpperCase() || '?'}</Text>
        </View>
        <View className='at-profile-copy'>
          <Text className='at-profile-name'>{displayName}</Text>
          <Text className='at-profile-sub'>账号与设置</Text>
        </View>
        <Icon name='chevron-right' size={20} color='#8E8E93' strokeWidth={1.8} />
      </View>
      </View>

      <AppDialog />
    </View>
  )
}
