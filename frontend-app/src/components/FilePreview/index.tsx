import { Image, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useEffect, useState } from 'react'

import Icon from '../Icon'
import { MarkdownText } from '../MarkdownText'
import {
  fileExtOf,
  isImageFile,
  isTextFile,
  openOutput,
  outputRawUrl,
  readTextPreview,
} from '../../services/files'

export interface PreviewFile {
  workspaceId: string
  path: string
  name: string
  ext?: string
}

interface FilePreviewProps {
  file: PreviewFile | null
  onClose: () => void
}

const MARKDOWN_EXT = new Set(['md', 'markdown'])

/** 应用内文件预览：图片 / 文本直接看；其余格式只提供原文下载，交给系统应用打开 */
export default function FilePreview({ file, onClose }: FilePreviewProps) {
  const [text, setText] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const ext = file ? (file.ext || fileExtOf(file.name)).toLowerCase() : ''
  const isText = !!file && isTextFile(ext) && !isImageFile(ext)
  const sourceKey = file ? `${file.workspaceId}:${file.path}` : ''

  useEffect(() => {
    setText('')
    setError('')
    if (!file || !isText) return
    let alive = true
    setLoading(true)
    readTextPreview({ workspace_id: file.workspaceId, rel_path: file.path })
      .then((value) => {
        if (alive) setText(value)
      })
      .catch((err: Error) => {
        if (alive) setError(err.message || '读取失败')
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
    // sourceKey 覆盖了文件身份，isText 决定是否请求
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sourceKey, isText])

  if (!file) return null

  const target = { workspace_id: file.workspaceId, rel_path: file.path }
  const rawUrl = outputRawUrl(target)

  const download = () => {
    openOutput({ ...target, ext }).catch((error: Error) => {
      Taro.showToast({ title: error.message || '下载失败', icon: 'none' })
    })
  }

  // 无法预览时的统一降级：只保证能拿到原文，交给手机自带应用打开
  const fallback = (title: string, desc: string) => (
    <View className='fp-fallback'>
      <Icon name='file-text' size={40} color='#8B95A8' />
      <Text className='fp-fallback-title'>{title}</Text>
      <Text className='fp-fallback-desc'>{desc}</Text>
      <View className='fp-fallback-btn' onClick={download}>
        <Text>下载原文</Text>
      </View>
    </View>
  )

  const body = () => {
    if (isImageFile(ext)) {
      return (
        <Image
          className='fp-image'
          src={rawUrl}
          mode='aspectFit'
          onClick={() => Taro.previewImage({ urls: [rawUrl] }).catch(() => undefined)}
        />
      )
    }
    if (isText) {
      if (loading) return <Text className='fp-state'>正在读取…</Text>
      if (error) return fallback('无法读取该文件', error)
      if (MARKDOWN_EXT.has(ext)) {
        return (
          <View className='fp-text'>
            <MarkdownText text={text} />
          </View>
        )
      }
      return (
        <View className='fp-text'>
          <Text className='fp-code' userSelect>
            {text}
          </Text>
        </View>
      )
    }
    return fallback('该格式无法在应用内预览', '下载后可用手机自带应用打开')
  }

  return (
    <View className='fp-mask'>
      <View className='fp-head'>
        <View className='fp-back' onClick={onClose}>
          <Icon name='chevron-left' size={22} color='#17191C' />
        </View>
        <Text className='fp-title'>{file.name}</Text>
        <View className='fp-download' onClick={download}>
          <Icon name='download' size={20} color='#17191C' />
        </View>
      </View>
      <View className='fp-body'>{body()}</View>
    </View>
  )
}
