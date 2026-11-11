import { Input, Text, View } from '@tarojs/components'
import Taro from '@tarojs/taro'
import { useState } from 'react'

import Icon from '../../components/Icon'
import { useThemeClass } from '../../hooks/useThemeClass'
import { apiOrigin, setApiOrigin } from '../../services/request'
import { useAuthStore } from '../../stores/useAuthStore'
import { toast } from '../../utils/toast'

const IS_H5 = process.env.TARO_ENV === 'h5'

export default function Login() {
  const login = useAuthStore((s) => s.login)
  const loggingIn = useAuthStore((s) => s.loggingIn)

  const [identifier, setIdentifier] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [agreed, setAgreed] = useState(false)
  const [error, setError] = useState('')
  const [passwordFocus, setPasswordFocus] = useState(false)
  // 小程序真机首登前必须先能配置服务器地址，否则默认 127.0.0.1 永远登录失败
  const [origin, setOrigin] = useState(apiOrigin())

  const handleLogin = () => {
    if (loggingIn) return
    if (!identifier.trim() || !password.trim()) {
      setError('请输入账号与密码')
      return
    }
    if (!agreed) {
      setError('请先阅读并勾选同意服务条款与隐私政策')
      return
    }
    setError('')
    login(identifier, password)
      .then(() => {
        Taro.reLaunch({ url: '/pages/home/index' })
      })
      .catch((e: Error) => {
        setError(e.message || '登录失败')
      })
  }

  return (
    <View className={useThemeClass('page')}>
      <View className='login'>
        <Text className='login-title'>登录 PrismClaw</Text>

        <View className='field'>
          <Text className='field-label'>账号</Text>
          <Input
            className='field-input'
            value={identifier}
            maxlength={64}
            placeholder='邮箱或手机号'
            placeholderClass='field-placeholder'
            confirmType='next'
            onConfirm={() => setPasswordFocus(true)}
            onInput={(e) => {
              setError('')
              setIdentifier(e.detail.value)
            }}
          />
        </View>
        <View className='field'>
          <View className='field-label-row'>
            <Text className='field-label'>密码</Text>
            <Text
              className='login-assist'
              onClick={() => void Taro.showToast({ title: '请联系管理员重置密码', icon: 'none' })}
            >
              忘记密码？
            </Text>
          </View>
          <View className='field-control'>
            <Input
              className='field-input is-with-action'
              value={password}
              password={!showPassword}
              maxlength={128}
              placeholder='请输入密码'
              placeholderClass='field-placeholder'
              focus={passwordFocus}
              onBlur={() => setPasswordFocus(false)}
              onInput={(e) => {
                setError('')
                setPassword(e.detail.value)
              }}
              onConfirm={handleLogin}
            />
            <View
              className='field-action'
              onClick={() => setShowPassword((value) => !value)}
            >
              <Icon
                name={showPassword ? 'eye-off' : 'eye'}
                size={18}
                color='#8E8E93'
                strokeWidth={1.7}
              />
            </View>
          </View>
        </View>

        {error ? (
          <View className='login-error'>
            <Icon name='alert-triangle' size={12} color='#D12224' />
            <Text>{error}</Text>
          </View>
        ) : null}

        <View className={`primary-btn${loggingIn ? ' is-disabled' : ''}`} onClick={handleLogin}>
          <Text>{loggingIn ? '登录中…' : '登录'}</Text>
        </View>

        {!IS_H5 ? (
          <View className='login-origin'>
            <Text className='field-label'>服务器地址</Text>
            <Input
              className='field-input'
              value={origin}
              placeholder='http://127.0.0.1:8000'
              placeholderClass='field-placeholder'
              onInput={(e) => setOrigin(e.detail.value)}
              onBlur={() => {
                setApiOrigin(origin.trim())
                toast('服务器地址已保存')
              }}
            />
            <Text className='login-origin-tip'>真机调试请填写电脑局域网 IP，保存后重新登录</Text>
          </View>
        ) : null}

        <View className='login-register'>
          <Text>还没有账号？</Text>
          <Text
            className='login-link'
            onClick={() => void Taro.showToast({ title: '账号由管理员统一创建', icon: 'none' })}
          >
            立即注册
          </Text>
        </View>

        <View className='login-agree'>
          <View
            className={`login-check-box${agreed ? ' is-on' : ''}`}
            onClick={() => {
              setError('')
              setAgreed((value) => !value)
            }}
          >
            {agreed ? <Icon name='check' size={11} color='#FFFFFF' strokeWidth={3} /> : null}
          </View>
          <Text className='login-agree-text'>
            我已阅读并同意
            <Text
              className='login-link'
              onClick={() => void Taro.showToast({ title: '服务条款由管理员提供', icon: 'none' })}
            >
              《服务条款》
            </Text>
            与
            <Text
              className='login-link'
              onClick={() => void Taro.showToast({ title: '隐私政策由管理员提供', icon: 'none' })}
            >
              《隐私政策》
            </Text>
          </Text>
        </View>
      </View>
    </View>
  )
}
