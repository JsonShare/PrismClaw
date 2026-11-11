import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from 'react'
import type { AuthTokenResponse } from '../types'
import { apiUrl } from '../lib/system-adapter'

const AUTH_STORAGE_KEY = 'prismclaw.auth.v1'

function sleep(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

interface StoredAuth {
  token: string | null
  userId: string | null
  nickname: string | null
}

function readStoredAuth(): StoredAuth {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY)
    if (!raw) return { token: null, userId: null, nickname: null }
    const parsed = JSON.parse(raw) as { token?: unknown; userId?: unknown; nickname?: unknown }
    return {
      token: typeof parsed.token === 'string' ? parsed.token : null,
      userId: typeof parsed.userId === 'string' ? parsed.userId : null,
      nickname: typeof parsed.nickname === 'string' ? parsed.nickname : null,
    }
  } catch {
    return { token: null, userId: null, nickname: null }
  }
}

async function readErrorMessage(res: Response): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown }
    if (typeof data.detail === 'string' && data.detail) return data.detail
  } catch {}
  return `请求失败（${res.status}）`
}

export interface RegisterPayload {
  nickname: string
  email: string
  code: string
  password: string
}

export interface ResetPasswordPayload {
  email: string
  code: string
  newPassword: string
}

interface AuthState {
  token: string | null
  userId: string | null
  nickname: string | null
  login: (userId: string, password: string) => Promise<void>
  sendEmailCode: (email: string, scene: 'register' | 'reset') => Promise<void>
  register: (payload: RegisterPayload) => Promise<void>
  resetPassword: (payload: ResetPasswordPayload) => Promise<void>
  logout: (reason?: string) => void
  loginError: string | null
  loading: boolean
}

const AuthContext = createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [token, setToken] = useState<string | null>(() => readStoredAuth().token)
  const [userId, setUserId] = useState<string | null>(() => readStoredAuth().userId)
  const [nickname, setNickname] = useState<string | null>(() => readStoredAuth().nickname)
  const [loginError, setLoginError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const login = useCallback(async (uid: string, password: string) => {
    setLoading(true)
    setLoginError(null)
    try {
      const url = apiUrl('/api/auth/token')
      let res: Response | null = null
      let lastError: unknown = null
      for (const backoffMs of [0, 200, 400, 800, 1200, 2000, 3000]) {
        if (backoffMs > 0) await sleep(backoffMs)
        try {
          res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ identifier: uid, password }),
          })
          lastError = null
          break
        } catch (err) {
          lastError = err
        }
      }
      if (!res) throw lastError ?? new Error('登录失败')
      if (!res.ok) {
        throw new Error(await readErrorMessage(res))
      }
      const data: AuthTokenResponse = await res.json()
      const resolvedId = data.user_id || uid
      const resolvedNick = data.nickname || resolvedId
      setToken(data.token)
      setUserId(resolvedId)
      setNickname(resolvedNick)

      try {
        localStorage.setItem(
          AUTH_STORAGE_KEY,
          JSON.stringify({ token: data.token, userId: resolvedId, nickname: resolvedNick }),
        )
      } catch {}
    } catch (err: any) {
      console.error('Login error details:', {
        message: err.message,
        name: err.name,
        stack: err.stack,
        url: apiUrl('/api/auth/token'),
      })
      setLoginError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const sendEmailCode = useCallback(async (email: string, scene: 'register' | 'reset') => {
    const res = await fetch(apiUrl('/api/auth/email-code'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, scene }),
    })
    if (!res.ok) throw new Error(await readErrorMessage(res))
  }, [])

  const register = useCallback(async (payload: RegisterPayload) => {
    const res = await fetch(apiUrl('/api/auth/register'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    if (!res.ok) throw new Error(await readErrorMessage(res))
  }, [])

  const resetPassword = useCallback(async (payload: ResetPasswordPayload) => {
    const res = await fetch(apiUrl('/api/auth/reset-password'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        email: payload.email,
        code: payload.code,
        new_password: payload.newPassword,
      }),
    })
    if (!res.ok) throw new Error(await readErrorMessage(res))
  }, [])

  const logout = useCallback((reason?: string) => {
    setToken(null)
    setUserId(null)
    setNickname(null)
    setLoginError(reason ?? null)
    try {
      localStorage.removeItem(AUTH_STORAGE_KEY)
    } catch {}
  }, [])

  useEffect(() => {
    if (typeof window === 'undefined') return
    const originalFetch = window.fetch?.bind(window)
    if (!originalFetch) return

    let active = true

    const getAuthHeader = (input: RequestInfo | URL, init?: RequestInit): string => {
      const fromInit = init?.headers
      if (fromInit) {
        if (fromInit instanceof Headers) {
          return fromInit.get('Authorization') || fromInit.get('authorization') || ''
        }
        if (Array.isArray(fromInit)) {
          const found = fromInit.find(([k]) => k.toLowerCase() === 'authorization')
          return found ? String(found[1] ?? '') : ''
        }
        if (typeof fromInit === 'object') {
          const obj = fromInit as Record<string, unknown>
          const value = obj.Authorization ?? obj.authorization
          return typeof value === 'string' ? value : ''
        }
      }
      if (input instanceof Request) {
        return input.headers.get('Authorization') || input.headers.get('authorization') || ''
      }
      return ''
    }

    const getUrl = (input: RequestInfo | URL): string => {
      if (typeof input === 'string') return input
      if (input instanceof Request) return input.url
      if (input instanceof URL) return input.toString()
      return ''
    }

    window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
      const res = await originalFetch(input as any, init as any)
      try {
        if (!active) return res
        const auth = getAuthHeader(input, init)
        const url = getUrl(input)
        if (
          res.status === 401 &&
          auth.trim().toLowerCase().startsWith('bearer ') &&
          !url.includes('/api/auth/token')
        ) {
          logout('登录已过期，请重新登录')
        }
      } catch {}
      return res
    }

    return () => {
      active = false
      window.fetch = originalFetch
    }
  }, [logout])

  return (
    <AuthContext.Provider
      value={{
        token,
        userId,
        nickname,
        login,
        sendEmailCode,
        register,
        resetPassword,
        logout,
        loginError,
        loading,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}
