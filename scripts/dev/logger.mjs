/**
 * 前端本地调试用的滚动日志（零依赖）。
 *
 * - 行格式：`YYYY-MM-DD HH:mm:ss.SSS | LEVEL | message`
 * - 日期滚动：跨天后的第一条日志，把前一天内容归档为 frontend-20261110.log
 * - 大小滚动：单文件超过 maxBytes 立即归档，同日追加序号
 *   frontend-20261110.1.log、frontend-20261110.2.log …
 * - 归档通过「复制 + 截断」实现：启动脚本用 O_APPEND 重定向了同一文件，
 *   截断可以保留 inode，崩溃输出不会落到已删除的旧文件里。
 * - 只保留最近 keep 个归档。
 */
import {
  appendFileSync,
  copyFileSync,
  existsSync,
  mkdirSync,
  readdirSync,
  statSync,
  truncateSync,
  unlinkSync,
} from 'node:fs'
import { basename, dirname, extname, join } from 'node:path'

const DEFAULT_MAX_BYTES = 10 * 1024 * 1024
const DEFAULT_KEEP = 14

const pad = (value, width = 2) => String(value).padStart(width, '0')

const dayStamp = (d) => `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`

const timestamp = (d) =>
  `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
  `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`

export function createLogger(file, { maxBytes = DEFAULT_MAX_BYTES, keep = DEFAULT_KEEP } = {}) {
  const dir = dirname(file)
  const base = basename(file)
  const ext = extname(base)
  const stem = ext ? base.slice(0, -ext.length) : base
  mkdirSync(dir, { recursive: true })

  let period = dayStamp(new Date())
  let size = existsSync(file) ? statSync(file).size : 0

  const archive = () => {
    if (!existsSync(file) || size === 0) return
    let target = join(dir, `${stem}-${period}${ext}`)
    let seq = 1
    while (existsSync(target)) target = join(dir, `${stem}-${period}.${seq++}${ext}`)
    copyFileSync(file, target)
    truncateSync(file, 0)
    size = 0
    prune()
  }

  const prune = () => {
    const prefix = `${stem}-`
    const archives = readdirSync(dir)
      .filter((name) => name.startsWith(prefix) && name.endsWith(ext) && name !== base)
      .map((name) => join(dir, name))
      .sort((a, b) => statSync(a).mtimeMs - statSync(b).mtimeMs)
    for (const old of archives.slice(0, Math.max(0, archives.length - keep))) {
      try {
        unlinkSync(old)
      } catch {
        // 清理失败不影响主流程
      }
    }
  }

  const write = (level, message) => {
    const now = new Date()
    const today = dayStamp(now)
    if (today !== period || size >= maxBytes) {
      archive()
      period = today
    }
    const line = `${timestamp(now)} | ${level.padEnd(7)} | ${message}\n`
    appendFileSync(file, line, 'utf8')
    size += Buffer.byteLength(line)
  }

  return {
    file,
    info: (message) => write('INFO', message),
    warn: (message) => write('WARN', message),
    error: (message) => write('ERROR', message),
  }
}
