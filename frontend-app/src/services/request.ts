import Taro from '@tarojs/taro'

/**
 * 统一 HTTP 客户端。
 *
 * - H5：与后端同源部署（后端直接托管 SPA 产物），默认使用相对路径。
 * - 小程序：没有同源概念，必须直连后端绝对地址；开发者可在
 *   「我的 → 服务器」里写入 origin 覆盖（存 storage，无需改代码）。
 */

const TOKEN_KEY = 'pc_token'
const ORIGIN_KEY = 'pc_api_origin'
const WEAPP_DEFAULT_ORIGIN = 'http://127.0.0.1:8000'

export class ApiError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

let unauthorizedHandler: (() => void) | null = null

/** 401 统一处理：由 auth store 注册，触发登出与跳转。 */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  unauthorizedHandler = handler
}

export function apiOrigin(): string {
  const saved = Taro.getStorageSync(ORIGIN_KEY)
  if (typeof saved === 'string' && saved.trim()) {
    return saved.trim().replace(/\/+$/, '')
  }
  return process.env.TARO_ENV === 'h5' ? '' : WEAPP_DEFAULT_ORIGIN
}

export function setApiOrigin(origin: string) {
  const value = origin.trim().replace(/\/+$/, '')
  if (value) Taro.setStorageSync(ORIGIN_KEY, value)
  else Taro.removeStorageSync(ORIGIN_KEY)
}

export function apiUrl(path: string): string {
  return `${apiOrigin()}${path}`
}

/** 给静态资源 URL 追加 _token（图片/下载等无法携带请求头的场景）。 */
export function withToken(url: string): string {
  const token = getToken()
  if (!token) return url
  return `${url}${url.includes('?') ? '&' : '?'}_token=${encodeURIComponent(token)}`
}

export function wsUrl(): string {
  const origin = apiOrigin()
  if (!origin) {
    // H5 同源：用当前页面地址推导 ws(s)://host/api/ws
    const loc = window.location
    return `${loc.protocol === 'https:' ? 'wss:' : 'ws:'}//${loc.host}/api/ws`
  }
  return `${origin.replace(/^http/, 'ws')}/api/ws`
}

export function getToken(): string {
  const token = Taro.getStorageSync(TOKEN_KEY)
  return typeof token === 'string' ? token : ''
}

export function setToken(token: string) {
  Taro.setStorageSync(TOKEN_KEY, token)
}

export function clearToken() {
  Taro.removeStorageSync(TOKEN_KEY)
}

type QueryValue = string | number | boolean | null | undefined

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  query?: Record<string, QueryValue>
  body?: unknown
  /** 默认 true；登录等公开接口可关闭。 */
  auth?: boolean
}

function buildQuery(query?: Record<string, QueryValue>): string {
  if (!query) return ''
  const parts: string[] = []
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === '') continue
    parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(value))}`)
  }
  return parts.length ? `?${parts.join('&')}` : ''
}

function errorMessage(status: number, data: unknown): string {
  if (data && typeof data === 'object') {
    const detail = (data as Record<string, unknown>).detail
    if (typeof detail === 'string' && detail) return detail
    if (Array.isArray(detail)) {
      return detail
        .map((item) =>
          item && typeof item === 'object' && typeof (item as Record<string, unknown>).msg === 'string'
            ? String((item as Record<string, unknown>).msg)
            : '参数不合法',
        )
        .join('；')
    }
    const message = (data as Record<string, unknown>).message
    if (typeof message === 'string' && message) return message
  }
  return `请求失败（${status}）`
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', query, body, auth = true } = options
  const token = auth ? getToken() : ''
  const header: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) header['Content-Type'] = 'application/json'
  if (token) header.Authorization = `Bearer ${token}`

  let res
  try {
    res = await Taro.request({
      url: apiUrl(`${path}${buildQuery(query)}`),
      method,
      data: body as Record<string, unknown> | undefined,
      header,
    })
  } catch (error) {
    throw new ApiError(0, '网络连接失败，请检查服务器地址')
  }

  const { statusCode, data } = res
  if (statusCode >= 200 && statusCode < 300) {
    return data as T
  }
  if (statusCode === 401 && auth) {
    unauthorizedHandler?.()
  }
  throw new ApiError(statusCode, errorMessage(statusCode, data))
}

export const http = {
  get: <T>(path: string, query?: RequestOptions['query']) => request<T>(path, { query }),
  post: <T>(path: string, body?: unknown, query?: RequestOptions['query']) =>
    request<T>(path, { method: 'POST', body, query }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
