/**
 * 结构化数据原生呈现
 *
 * 复用 PatientReport 已存在的 `labs` / `medications` 字段（上传解析即填充，
 * HIS 接入后同样会灌入这些字段）。把它从「塞进 AI 摘要」里拆出来，单独做
 * 原生表格 / 列表 —— 这正是 HIS 结构化呈现的同一套路径：
 * 数据有了就渲染，没有就整块不出现（返回 null），不伪造。
 */

import type { PatientReport } from '../../types/view'

export function StructuredData({ report }: { report: PatientReport }) {
  const labs = report.labs ?? []
  const meds = report.medications ?? []
  if (labs.length === 0 && meds.length === 0) return null

  return (
    <>
      {labs.length > 0 && (
        <div className="section struct-section">
          <div className="section-head">
            <div className="section-icon blue">数</div>
            <span className="section-title">结构化检验</span>
          </div>
          <div className="lab-table-wrap">
          <table className="lab-table">
            <thead>
              <tr>
                <th>项目</th>
                <th>结果</th>
                <th>参考</th>
                <th>标志</th>
              </tr>
            </thead>
            <tbody>
              {labs.map((l, i) => (
                <tr key={i} className={l.flag === 'H' || l.flag === 'L' ? 'lab-flag' : ''}>
                  <td>{l.item}</td>
                  <td>
                    {l.valueText}
                    {l.unit ? ` ${l.unit}` : ''}
                  </td>
                  <td className="lab-ref">{l.refText}</td>
                  <td className={`lab-flag-cell ${l.flag ? `flag-${l.flag}` : ''}`}>
                    {l.flag || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </div>
      )}

      {meds.length > 0 && (
        <div className="section struct-section">
          <div className="section-head">
            <div className="section-icon green">药</div>
            <span className="section-title">用药记录</span>
          </div>
          <ul className="med-list">
            {meds.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}
