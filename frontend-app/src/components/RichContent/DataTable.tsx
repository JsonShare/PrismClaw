import { Text, View } from '@tarojs/components'
import { useMemo } from 'react'

import CodeBlock from './CodeBlock'
import { normalizeDataSpec } from '../../lib/richText'

interface DataTableProps {
  code: string
}

/** datatable / spreadsheet 富块：JSON 契约渲染为可横向滚动的表格 */
export default function DataTable({ code }: DataTableProps) {
  const spec = useMemo(() => normalizeDataSpec(code), [code])
  if (!spec) return <CodeBlock code={code} lang='datatable' />

  return (
    <View className='rich-frame'>
      {spec.title ? (
        <View className='rich-frame-head'>
          <Text className='rich-frame-title'>{spec.title}</Text>
        </View>
      ) : null}
      <View className='rich-frame-body rich-table-scroll'>
        <View className='rich-table'>
          <View className='rich-table-row is-head'>
            {spec.columns.map((column, index) => (
              <Text className='rich-table-cell' key={`head-${index}`}>
                {column}
              </Text>
            ))}
          </View>
          {spec.rows.map((row, rowIndex) => (
            <View className='rich-table-row' key={`row-${rowIndex}`}>
              {spec.columns.map((_, cellIndex) => (
                <Text className='rich-table-cell' key={`row-${rowIndex}-${cellIndex}`}>
                  {row[cellIndex] ?? ''}
                </Text>
              ))}
            </View>
          ))}
        </View>
      </View>
    </View>
  )
}
