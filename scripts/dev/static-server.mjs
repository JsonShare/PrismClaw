#!/usr/bin/env node
/**
 * 本地静态托管 + API 反代，用来模拟生产环境的 nginx。
 *
 * - 托管指定目录的静态产物，未命中的 GET 回落到 index.html（SPA）
 * - /api（含 WebSocket 升级）反代到后端，保持与生产同源
 *
 * 用法：node scripts/dev/static-server.mjs --dir frontend/dist --port 5173 --api http://127.0.0.1:8000 \
 *        [--log logs/app/frontend.log]
 */
import http from 'node:http'
import { createReadStream, statSync } from 'node:fs'
import { extname, join, resolve, sep } from 'node:path'

import { createLogger } from './logger.mjs'

const argv = process.argv.slice(2)
const argOf = (name, fallback = '') => {
  const index = argv.indexOf(name)
  return index >= 0 && argv[index + 1] ? argv[index + 1] : fallback
}

const rootDir = resolve(argOf('--dir', 'dist'))
const port = Number(argOf('--port', '5173'))
const api = new URL(argOf('--api', 'http://127.0.0.1:8000'))
const logPath = argOf('--log', '')
const logger = logPath ? createLogger(resolve(logPath)) : null
const PROXY_PREFIXES = ['/api']

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.txt': 'text/plain; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.otf': 'font/otf',
  '.eot': 'application/vnd.ms-fontobject',
  '.wasm': 'application/wasm',
  '.mp4': 'video/mp4',
  '.webm': 'video/webm',
  '.pdf': 'application/pdf',
}

const proxyHeaders = (headers) => ({ ...headers, host: api.host })

/** 访问日志：请求结束时记录方法、路径、状态码与耗时 */
const logAccess = (req, res, startedAt) => {
  if (!logger) return
  const elapsed = Date.now() - startedAt
  const line = `${req.method} ${req.url} ${res.statusCode} ${elapsed}ms`
  if (res.statusCode >= 500) logger.error(line)
  else if (res.statusCode >= 400) logger.warn(line)
  else logger.info(line)
}

const sendStatic = (res, filePath, status = 200) => {
  const ext = extname(filePath).toLowerCase()
  const immutable = /\/assets\/|\.[0-9a-f]{8,}\./i.test(filePath)
  res.writeHead(status, {
    'Content-Type': MIME[ext] || 'application/octet-stream',
    'Cache-Control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
  })
  createReadStream(filePath).pipe(res)
}

const server = http.createServer((req, res) => {
  const startedAt = Date.now()
  res.on('finish', () => logAccess(req, res, startedAt))
  const url = req.url || '/'
  if (PROXY_PREFIXES.some((prefix) => url.startsWith(prefix))) {
    const upstream = http.request(
      {
        hostname: api.hostname,
        port: api.port || 80,
        path: url,
        method: req.method,
        headers: proxyHeaders(req.headers),
      },
      (upstreamRes) => {
        res.writeHead(upstreamRes.statusCode || 502, upstreamRes.headers)
        upstreamRes.pipe(res)
      },
    )
    upstream.on('error', () => {
      res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' })
      res.end('后端未就绪：' + api.origin)
    })
    req.pipe(upstream)
    return
  }

  if (req.method !== 'GET' && req.method !== 'HEAD') {
    res.writeHead(405, { Allow: 'GET, HEAD' })
    res.end()
    return
  }

  let pathname = '/'
  try {
    pathname = decodeURIComponent(url.split('?')[0])
  } catch {
    res.writeHead(400)
    res.end()
    return
  }
  const target = resolve(join(rootDir, pathname))
  if (!target.startsWith(rootDir + sep) && target !== rootDir) {
    res.writeHead(403)
    res.end()
    return
  }

  try {
    if (statSync(target).isFile()) {
      sendStatic(res, target)
      return
    }
  } catch {
    // 未命中文件，继续走 SPA 回落
  }

  try {
    const index = join(rootDir, 'index.html')
    statSync(index)
    sendStatic(res, index)
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
    res.end('Not found: ' + pathname)
  }
})

server.on('upgrade', (req, socket, head) => {
  const url = req.url || '/'
  if (!PROXY_PREFIXES.some((prefix) => url.startsWith(prefix))) {
    socket.destroy()
    return
  }
  const upstream = http.request({
    hostname: api.hostname,
    port: api.port || 80,
    path: url,
    method: 'GET',
    headers: proxyHeaders(req.headers),
  })
  upstream.on('upgrade', (upstreamRes, upstreamSocket, upstreamHead) => {
    const lines = [`HTTP/1.1 101 ${upstreamRes.statusMessage || 'Switching Protocols'}`]
    for (let i = 0; i < upstreamRes.rawHeaders.length; i += 2) {
      lines.push(`${upstreamRes.rawHeaders[i]}: ${upstreamRes.rawHeaders[i + 1]}`)
    }
    socket.write(lines.join('\r\n') + '\r\n\r\n')
    if (upstreamHead?.length) socket.write(upstreamHead)
    if (head?.length) upstreamSocket.write(head)
    upstreamSocket.pipe(socket)
    socket.pipe(upstreamSocket)
    upstreamSocket.on('error', () => socket.destroy())
    socket.on('error', () => upstreamSocket.destroy())
  })
  upstream.on('error', () => socket.destroy())
  upstream.end()
})

server.on('error', (error) => {
  const line = `监听 ${port} 失败：${error.message}`
  if (logger) logger.error(line)
  else console.error(line)
  process.exit(1)
})

server.listen(port, '0.0.0.0', () => {
  const line = `[static] ${rootDir} -> http://localhost:${port} (api -> ${api.origin})`
  if (logger) logger.info(line)
  else console.log(line)
})

const crash = (error) => {
  const line = error?.stack || String(error)
  if (logger) logger.error(line)
  else console.error(line)
  process.exit(1)
}
process.on('uncaughtException', crash)
process.on('unhandledRejection', crash)
