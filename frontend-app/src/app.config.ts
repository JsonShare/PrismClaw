export default defineAppConfig({
  animation: false,
  pages: [
    'pages/home/index',
    'pages/tasks/index',
    'pages/files/index',
    'pages/automations/index',
    'pages/chat/index',
    'pages/scheduled/index',
    'pages/me/index',
    'pages/login/index'
  ],
  window: {
    // 顶栏自绘（状态栏高度由 AppHeader 内联），只保留下拉背景与标题兜底
    backgroundTextStyle: 'light',
    backgroundColor: '#FFFFFF',
    navigationBarTitleText: 'PrismWork',
    navigationStyle: 'custom'
  }
})
