import { Input, ScrollView, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'

import AppDialog from '../../components/AppDialog'
import AppHeader from '../../components/AppHeader'
import EmptyState from '../../components/EmptyState'
import FileIcon from '../../components/FileIcon'
import FilePreview from '../../components/FilePreview'
import type { PreviewFile } from '../../components/FilePreview'
import Icon from '../../components/Icon'
import { useThemeClass } from '../../hooks/useThemeClass'
import type { FileCategory, OutputFile } from '../../services/files'
import { fileExtOf, openOutput } from '../../services/files'
import { useAppStore } from '../../stores/useAppStore'
import { useFileStore } from '../../stores/useFileStore'
import { formatRelative, formatSize } from '../../utils/format'
import { goBack, toast } from '../../utils/toast'

type TypeFilter = FileCategory | 'all'
type SortKey = 'mtime' | 'size' | 'session' | 'workspace'

// 与 PC 端 OUTPUT_TYPE_FILTERS 保持一致
const TYPE_OPTIONS: { value: TypeFilter; label: string }[] = [
  { value: 'all', label: '全部类型' },
  { value: 'document', label: '文档' },
  { value: 'spreadsheet', label: '表格' },
  { value: 'slide', label: '幻灯片' },
  { value: 'image', label: '图片' },
  { value: 'web', label: '网页' },
  { value: 'code', label: '代码' },
  { value: 'other', label: '其他' }
]

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'mtime', label: '按更新时间' },
  { value: 'size', label: '按文件大小' },
  { value: 'session', label: '按任务分组' },
  { value: 'workspace', label: '按工作区分组' }
]

const ALL_WORKSPACES = 'all'
const fileKey = (file: OutputFile) => `${file.workspace_id}/${file.rel_path}`

export default function Files() {
  const items = useFileStore((s) => s.items)
  const loading = useFileStore((s) => s.loading)
  const load = useFileStore((s) => s.load)
  const remove = useFileStore((s) => s.remove)
  const openDialog = useAppStore((s) => s.openDialog)

  const [typeFilter, setTypeFilter] = useState<TypeFilter>('all')
  const [sortKey, setSortKey] = useState<SortKey>('mtime')
  const [menu, setMenu] = useState<'type' | 'sort' | null>(null)
  const [searchOpen, setSearchOpen] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [error, setError] = useState('')
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  const [preview, setPreview] = useState<PreviewFile | null>(null)

  const refresh = () => {
    setError('')
    load(ALL_WORKSPACES, true)
      .then(() => setError(''))
      .catch((e: Error) => setError(e.message))
  }

  useEffect(() => {
    refresh()
  }, [load])

  const typeLabel = TYPE_OPTIONS.find((o) => o.value === typeFilter)?.label || '全部类型'
  const sortLabel = SORT_OPTIONS.find((o) => o.value === sortKey)?.label || '按更新时间'
  const isGrouped = sortKey === 'session' || sortKey === 'workspace'

  const q = keyword.trim().toLowerCase()
  const filtered = items
    .filter((file) => {
      if (typeFilter !== 'all' && file.type !== typeFilter) return false
      if (q && !file.name.toLowerCase().includes(q)) return false
      return true
    })
    .sort((a, b) => {
      if (sortKey === 'size') return b.size_bytes - a.size_bytes
      if (sortKey === 'session') {
        const at = a.source_session_title || a.source_session_id || ''
        const bt = b.source_session_title || b.source_session_id || ''
        return bt.localeCompare(at)
      }
      if (sortKey === 'workspace') return (b.workspace_name || '').localeCompare(a.workspace_name || '')
      return b.mtime_ms - a.mtime_ms
    })

  const groups = (() => {
    if (!isGrouped) return null
    const map = new Map<string, { key: string; label: string; files: OutputFile[] }>()
    for (const file of filtered) {
      const key =
        sortKey === 'workspace'
          ? file.workspace_id
          : file.source_session_id || '__unlinked__'
      const label =
        sortKey === 'workspace'
          ? file.workspace_name || '未命名工作区'
          : file.source_session_title ||
            (file.source_session_id ? `会话 ${file.source_session_id.slice(0, 8)}` : '未关联任务')
      const group = map.get(key)
      if (group) group.files.push(file)
      else map.set(key, { key, label, files: [file] })
    }
    return Array.from(map.values())
  })()

  const previewFile = (file: OutputFile) => {
    setPreview({
      workspaceId: file.workspace_id,
      path: file.rel_path,
      name: file.name,
      ext: (file.ext || fileExtOf(file.name)).toLowerCase(),
    })
  }

  const openFile = (file: OutputFile) => {
    const action = (value: string) => {
      if (value === 'preview') {
        previewFile(file)
      } else if (value === 'open') {
        openOutput(file).catch((e: Error) => toast(e.message || '打开失败'))
      } else if (value === 'session' && file.source_session_id) {
        Taro.navigateTo({ url: `/pages/chat/index?id=${file.source_session_id}` })
      } else if (value === 'delete') {
        openDialog({
          kind: 'danger',
          title: `删除「${file.name}」？`,
          desc: '删除后文件将从服务器移除，且无法恢复。关联任务与会话记录会保留。',
          confirmText: '删除文件',
          onConfirm: () => {
            remove(file)
              .then(() => toast('已删除'))
              .catch((e: Error) => toast(e.message || '删除失败'))
          }
        })
      }
    }

    openDialog({
      kind: 'sheet',
      title: file.name,
      items: [
        { label: '预览文件', value: 'preview' },
        { label: '下载原文', value: 'open' },
        ...(file.source_session_id
          ? [{ label: '查看来源会话', value: 'session' }]
          : []),
        { label: '删除文件', value: 'delete', danger: true }
      ],
      onSelect: action
    })
  }

  const row = (file: OutputFile) => (
    <View className='file-row' key={fileKey(file)} onClick={() => previewFile(file)}>
      <View className='file-ico'>
        <FileIcon filename={file.name} ext={file.ext} size={24} className='file-ico-img' />
      </View>
      <View className='file-main'>
        <Text className='file-name'>{file.name}</Text>
        <Text className='file-meta'>
          {`${formatSize(file.size_bytes)} · ${formatRelative(file.mtime_ms)}${
            file.workspace_name ? ` · ${file.workspace_name}` : ''
          }`}
        </Text>
      </View>
      <View
        className='file-more'
        onClick={(e) => {
          e.stopPropagation()
          openFile(file)
        }}
      >
        <Icon name='more-horizontal' size={20} color='#5B6068' />
      </View>
    </View>
  )

  const groupHeader = (group: { key: string; label: string; files: OutputFile[] }) => {
    const isCollapsed = !!collapsed[group.key]
    return (
      <View
        className='files-group-head'
        key={`head-${group.key}`}
        onClick={() => setCollapsed((prev) => ({ ...prev, [group.key]: !isCollapsed }))}
      >
        <Icon
          name='chevron-down'
          size={14}
          color='#9AA0A8'
          style={{
            transform: isCollapsed ? 'rotate(-90deg)' : 'none',
            transition: 'transform 160ms cubic-bezier(0.2, 0.8, 0.2, 1)',
          }}
        />
        <Text className='files-group-title'>{group.label}</Text>
        <Text className='files-group-count'>{`${group.files.length} 个文件`}</Text>
      </View>
    )
  }

  const renderList = () => {
    if (loading && items.length === 0) {
      return <EmptyState title='正在加载文件…' desc='从服务器同步产出物' />
    }
    if (error && items.length === 0) {
      return (
        <EmptyState title='文件加载失败' desc={error}>
          <View className='empty-action' onClick={refresh}>
            <Text>重新加载</Text>
          </View>
        </EmptyState>
      )
    }
    if (!filtered.length) {
      return (
        <EmptyState
          title={items.length ? '没有匹配的文件' : '暂无产出物'}
          desc={items.length ? '换个关键词或筛选条件试试' : '任务执行生成的文件会出现在这里'}
        />
      )
    }
    if (groups) {
      return (
        <View className='files-list'>
          {groups.map((group) => (
            <View key={group.key}>
              {groupHeader(group)}
              {collapsed[group.key] ? null : group.files.map(row)}
            </View>
          ))}
        </View>
      )
    }
    return <View className='files-list'>{filtered.map(row)}</View>
  }

  return (
    <View className={useThemeClass('page files-page')}>
      <AppHeader
        variant='settings'
        title='我的文件'
        onBack={() => goBack('/pages/tasks/index')}
        onRefresh={refresh}
      />

      <View className='files-toolbar'>
        <View className='files-filters'>
          <View
            className={`files-trigger${menu === 'type' ? ' is-open' : ''}`}
            onClick={() => setMenu(menu === 'type' ? null : 'type')}
          >
            <Text>{typeLabel}</Text>
            <Icon name='chevron-down' size={14} color='#575B62' />
          </View>
          <View className='files-filters-right'>
            <View
              className={`files-trigger${menu === 'sort' ? ' is-open' : ''}`}
              onClick={() => setMenu(menu === 'sort' ? null : 'sort')}
            >
              <Icon name='clock' size={14} color='#575B62' />
              <Text>{sortLabel}</Text>
              <Icon name='chevron-down' size={14} color='#575B62' />
            </View>
            <View
              className='files-search-btn'
              onClick={() => {
                setMenu(null)
                setSearchOpen((value) => {
                  // 关闭搜索时清空关键词，避免列表仍被隐藏条件过滤
                  if (value) setKeyword('')
                  return !value
                })
              }}
            >
              <Icon name='search' size={18} color='#17191C' />
            </View>
          </View>

          {menu ? <View className='files-menu-mask' onClick={() => setMenu(null)} /> : null}
          {menu === 'type' ? (
            <View className='files-menu is-type'>
              {TYPE_OPTIONS.map((option) => (
                <View
                  className={`files-menu-item${option.value === typeFilter ? ' is-on' : ''}`}
                  key={option.value}
                  onClick={() => {
                    setTypeFilter(option.value)
                    setMenu(null)
                  }}
                >
                  <Text>{option.label}</Text>
                </View>
              ))}
            </View>
          ) : null}
          {menu === 'sort' ? (
            <View className='files-menu is-sort'>
              {SORT_OPTIONS.map((option) => (
                <View
                  className={`files-menu-item${option.value === sortKey ? ' is-on' : ''}`}
                  key={option.value}
                  onClick={() => {
                    setSortKey(option.value)
                    setMenu(null)
                  }}
                >
                  <Text>{option.label}</Text>
                </View>
              ))}
            </View>
          ) : null}
        </View>

        {searchOpen ? (
          <View className='files-search'>
            <Icon name='search' size={15} color='#838C9E' />
            <Input
              className='files-input'
              value={keyword}
              focus
              placeholder='搜索文件名'
              onInput={(e) => setKeyword(e.detail.value)}
            />
            {keyword ? (
              <View className='files-search-clear' onClick={() => setKeyword('')}>
                <Icon name='x' size={14} color='#838C9E' />
              </View>
            ) : null}
          </View>
        ) : null}

        {error && items.length ? (
          <View className='files-count-row'>
            <Text className='files-error-text' onClick={refresh}>
              同步失败，点击重试
            </Text>
          </View>
        ) : null}
      </View>

      <ScrollView scrollY className='app-body' style={{ height: 0, flex: 1, minHeight: 0 }}>
        {renderList()}
      </ScrollView>

      <AppDialog />

      <FilePreview file={preview} onClose={() => setPreview(null)} />
    </View>
  )
}
