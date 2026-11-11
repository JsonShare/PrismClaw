/// <reference types="@tarojs/taro" />

declare module '*.scss'
declare module '*.css'
declare module '*.png'

declare namespace NodeJS {
  interface ProcessEnv {
    TARO_ENV: 'weapp' | 'swan' | 'alipay' | 'h5' | 'rn' | 'tt' | 'quickapp' | 'qq' | 'jd'
    TARO_APP_ID: string
    NODE_ENV: 'development' | 'production'
  }
}
