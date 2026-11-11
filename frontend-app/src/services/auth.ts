import type { AuthTokenResponse } from '../types'
import { http, request } from './request'

export interface UserProfile {
  user_id: string
}

export async function login(identifier: string, password: string): Promise<AuthTokenResponse> {
  // 登录接口的 401 是“账号或密码错误”，不能触发全局登出与跳转
  return request<AuthTokenResponse>('/api/auth/token', {
    method: 'POST',
    body: { identifier, password },
    auth: false,
  })
}

export async function fetchProfile(): Promise<UserProfile> {
  return http.get<UserProfile>('/api/profile')
}
