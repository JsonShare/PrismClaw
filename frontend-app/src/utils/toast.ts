import Taro from '@tarojs/taro'

export function toast(title: string) {
  Taro.showToast({ title, icon: 'none', duration: 2200 })
}

/** 返回上一页；无历史时回退到指定页面 */
export function goBack(fallback: string) {
  const pages = Taro.getCurrentPages()
  if (pages.length > 1) {
    Taro.navigateBack()
  } else {
    Taro.redirectTo({ url: fallback })
  }
}

export function goHome() {
  Taro.reLaunch({ url: '/pages/home/index' })
}
