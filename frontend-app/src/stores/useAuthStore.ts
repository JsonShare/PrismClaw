import Taro from '@tarojs/taro'
import { create } from 'zustand'

import { fetchProfile, login as loginRequest } from '../services/auth'
import { ApiError, clearToken, getToken, setToken, setUnauthorizedHandler } from '../services/request'
import { socket } from '../services/socket'

const NICKNAME_KEY = 'pc_nickname'
const USER_ID_KEY = 'pc_user_id'

function readStored(key: string): string {
  const value = Taro.getStorageSync(key)
  return typeof value === 'string' ? value : ''
}

interface AuthStore {
  token: string
  userId: string
  nickname: string
  ready: boolean
  loggingIn: boolean

  bootstrap: () => Promise<boolean>
  login: (identifier: string, password: string) => Promise<void>
  logout: () => void
}

export const useAuthStore = create<AuthStore>((set, get) => {
  const redirectToLogin = () => {
    clearToken()
    Taro.removeStorageSync(NICKNAME_KEY)
    Taro.removeStorageSync(USER_ID_KEY)
    socket.disconnect()
    set({ token: '', userId: '', nickname: '', ready: true })
    const pages = Taro.getCurrentPages()
    const current = pages[pages.length - 1]
    if (!current || current.route !== 'pages/login/index') {
      Taro.reLaunch({ url: '/pages/login/index' })
    }
  }

  setUnauthorizedHandler(redirectToLogin)
  socket.setAuthFailureHandler(redirectToLogin)

  return {
    token: getToken(),
    userId: readStored(USER_ID_KEY),
    nickname: readStored(NICKNAME_KEY),
    ready: false,
    loggingIn: false,

    bootstrap: async () => {
      const token = getToken()
      if (!token) {
        set({ ready: true, token: '', userId: '', nickname: '' })
        return false
      }
      try {
        const profile = await fetchProfile()
        set({
          token,
          ready: true,
          userId: profile.user_id,
          nickname: readStored(NICKNAME_KEY) || profile.user_id,
        })
        socket.connect()
        return true
      } catch (error) {
        // 只有凭证失效才登出；断网/后端 5xx 时保留登录态，进入应用后由页面重试
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          redirectToLogin()
          return false
        }
        set({
          token,
          ready: true,
          userId: readStored(USER_ID_KEY),
          nickname: readStored(NICKNAME_KEY),
        })
        return true
      }
    },

    login: async (identifier, password) => {
      if (get().loggingIn) return
      set({ loggingIn: true })
      try {
        const res = await loginRequest(identifier.trim(), password)
        setToken(res.token)
        const nickname = res.nickname || res.user_id
        Taro.setStorageSync(USER_ID_KEY, res.user_id)
        Taro.setStorageSync(NICKNAME_KEY, nickname)
        set({ token: res.token, userId: res.user_id, nickname, ready: true })
        socket.connect()
      } finally {
        set({ loggingIn: false })
      }
    },

    logout: redirectToLogin,
  }
})
