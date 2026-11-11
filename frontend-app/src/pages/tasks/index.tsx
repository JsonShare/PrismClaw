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

export default function Tasks() {
  const conversations = useChatStore((s) => s.conversations)
  const listLoading = useChatStore((s) => s.listLoading)
  const listHasMore = useChatStore((s) => s.listHasMore)
  const loadConversations = useChatStore((s) => s.loadConversations)
  const loadAllConversations = useChatStore((s) => s.loadAllConversations)
  const removeConversation = useChatStore((s) => s.removeConversation)
  const renameConversation = useChatStore((s) => s.renameConversation)

  const tasks = useTaskStore((s) => s.tasks)
  const tasksLoading = useTaskStore((s) => s.loading)
  const loadTasks = useTaskStore((s) => s.load)
  const toggleTask = useTaskStore((s) => s.toggle)
  const triggerTask = useTaskStore((s) => s.trigger)
  const removeTask = useTaskStore((s) => s.remove)

  const workspaces = useAppStore((s) => s.workspaces)
  const loadWorkspaces = useAppStore((s) => s.loadWorkspaces)
  const openDialog = useAppStore((s) => s.openDialog)
  const nickname = useAuthStore((s) => s.nickname)

  const [scope, setScope] = useState(SCOPE_ALL)
  const [sorter, setSorter] = useState<'recent' | 'name'>('recent')
  const [keyword, setKeyword] = useState('')
  const [listError, setListError] = useState('')

  useEffect(() => {
    loadWorkspaces().catch(() => undefined)
  }, [])

  useEffect(() => {
    setListError('')
    loadConversations(scope, true).catch((error: Error) => setListError(error.message))
    loadTasks(scope).catch((error: Error) => toast(error.message))
  }, [scope, loadConversations, loadTasks])

  const retryList = () => {
    setListError('')
    loadConversations(scope, true).catch((error: Error) => setListError(error.message))
  }

  const loadMore = () => {
    // 直接读 store：滚动事件可能在同一帧内连续触发，闭包里的 listLoading 会滞后
    const state = useChatStore.getState()
    if (state.listLoading || !state.listHasMore) return
    loadConversations(scope, false).catch((error: Error) => toast(error.message))
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
    const filtered = keyword.trim()
      ? list.filter((row) => row.title.toLowerCase().includes(keyword.trim().toLowerCase()))
      : list
    return sorter === 'name'
      ? filtered.slice().sort((a, b) => a.title.localeCompare(b.title, 'zh-Hans-CN'))
      : filtered.slice().sort((a, b) => b.timeMs - a.timeMs)
  }, [conversations, tasks, keyword, sorter])

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

  const pickScope = () => {
    const open = () =>
      openDialog({
        kind: 'sheet',
        title: '切换工作区',
        items: [
          { label: '全部工作区', value: SCOPE_ALL, active: scope === SCOPE_ALL },
          ...workspaces.map((workspace) => ({
            label: workspace.name,
            value: workspace.id,
            active: workspace.id === scope,
          })),
        ],
        onSelect: (value) => setScope(value),
      })
    if (workspaces.length) {
      open()
      return
    }
    loadWorkspaces(true)
      .then(open)
      .catch((error: Error) => toast(error.message))
  }

  const pickSorter = () => {
    openDialog({
      kind: 'sheet',
      title: '列表排序',
      items: [
        { label: '最近活动', value: 'recent', active: sorter === 'recent' },
        { label: '按名称', value: 'name', active: sorter === 'name' },
      ],
      onSelect: (value) => setSorter(value === 'name' ? 'name' : 'recent'),
    })
  }

  const search = () => {
    openDialog({
      kind: 'form',
      title: '搜索任务',
      confirmText: '搜索',
      fields: [{ key: 'keyword', label: '关键词', value: keyword, placeholder: '输入会话或自动化名称' }],
      onConfirm: (values) => {
        const next = values.keyword.trim()
        setKeyword(next)
        // 关键词只在前端过滤，先把列表翻到底再过滤，避免漏掉首屏之外的任务
        if (next) loadAllConversations(scope).catch((error: Error) => toast(error.message))
      },
    })
  }

  const scopeLabel = workspaces.find((workspace) => workspace.id === scope)?.name || '全部任务'

  const goHome = () => {
    if (Taro.getCurrentPages().length > 1) Taro.navigateBack()
    else Taro.reLaunch({ url: '/pages/home/index' })
  }

  return (
    <View className={useThemeClass('page at-page')}>
      <View className='at-head'>
        <View className='at-title' onClick={pickScope}>
          <Text className='at-title-text'>{scopeLabel}</Text>
          <View className='at-caret' />
        </View>
        <View className='at-capsule'>
          <View className='at-icobtn' onClick={search}>
            <Icon name='search' size={20} color='#111114' strokeWidth={2.1} />
          </View>
          <View className='at-avatar' onClick={() => Taro.navigateTo({ url: '/pages/me/index' })}>
            <Text>{(nickname || 'P').slice(0, 1)}</Text>
          </View>
        </View>
      </View>

      <View className='at-entries'>
        <View className='at-entry' onClick={() => Taro.navigateTo({ url: '/pages/automations/index' })}>
          <Icon name='clock' size={22} color='#111114' strokeWidth={2} />
          <Text>自动化</Text>
        </View>
        <View className='at-entry' onClick={() => Taro.navigateTo({ url: '/pages/files/index' })}>
          <Icon name='folder' size={22} color='#111114' strokeWidth={2} />
          <Text>我的文件</Text>
        </View>
      </View>

      <View className='at-rule' />

      <View className='at-section'>
        <View className='at-sort' onClick={pickSorter}>
          <Text>{sorter === 'name' ? '按名称' : '最近活动'}</Text>
          <Icon name='chevron-down' size={14} color='#8E8E93' strokeWidth={2} />
        </View>
        {keyword ? (
          <View className='at-sort' onClick={() => setKeyword('')}>
            <Text>{`搜索：${keyword}`}</Text>
            <Icon name='x' size={14} color='#8E8E93' strokeWidth={2} />
          </View>
        ) : null}
      </View>

      <ScrollView
        scrollY
        className='at-scroll'
        style={{ height: 0, flex: 1, minHeight: 0 }}
        lowerThreshold={120}
        onScrollToLower={loadMore}
      >
        {rows.length ? (
          rows.map((row) => (
            <View
              className='at-row'
              key={row.key}
              onClick={() => openRow(row)}
              onLongPress={() => openRowActions(row)}
            >
              <View className='at-chip'>
                <Icon
                  name={row.kind === 'scheduled' ? 'clock' : 'message'}
                  size={20}
                  color='#111114'
                  strokeWidth={2}
                />
              </View>
              <View className='at-main'>
                <Text className='at-name'>{row.title}</Text>
                <View className='at-meta'>
                  <Icon
                    name={row.kind === 'scheduled' ? 'clock' : 'message'}
                    size={12}
                    color='#9A9AA0'
                  />
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
                  <Icon name='more-horizontal' size={16} color='#9A9AA0' strokeWidth={2} />
                </View>
              </View>
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
                {listLoading || tasksLoading
                  ? '加载中…'
                  : keyword
                    ? '没有匹配的任务'
                    : '这个工作区还没有任务'}
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

      <View className='at-fab' onClick={goHome}>
        <Icon name='message-plus' size={26} color='#FFFFFF' strokeWidth={1.9} />
      </View>

      <AppDialog />
    </View>
  )
}
