import Taro from '@tarojs/taro'

import type { WSClientMessage, WSServerMessage, WSState } from '../types'
import { getToken, wsUrl } from './request'

const PROTOCOL_VERSION = 1
const MAX_OUTBOX = 50
const MIN_BACKOFF = 1000
const MAX_BACKOFF = 10000
/** 服务端每 30s 下发一次 ping；超过该时长未收到任何帧则判定链路已死 */
const FRAME_STALE_TIMEOUT = 75000
const WATCHDOG_INTERVAL = 15000
const AUTH_FAILURE_CODES = new Set([4001, 4002, 4003, 4005])
/** 服务端单用户连接数已满（多为旧连接未被回收），降低重连频率避免雪崩 */
const BUSY_CODE = 4004

type FrameListener = (frame: WSServerMessage) => void
type StateListener = (state: WSState) => void

/**
 * 全局单例 WebSocket。
 *
 * 一个用户只需要一条连接：会话帧都带 conversation_id，聊天页按 id 过滤，
 * 其余页面共享同一连接即可（自动化/任务页也能感知 run 状态）。
 */
class AppSocket {
  private task: Taro.SocketTask | null = null
  private state: WSState = 'disconnected'
  private outbox: WSClientMessage[] = []
  private frameListeners = new Set<FrameListener>()
  private stateListeners = new Set<StateListener>()
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null
  private readyTimer: ReturnType<typeof setTimeout> | null = null
  private watchdogTimer: ReturnType<typeof setInterval> | null = null
  /** 最近一次收到任意服务端帧的时间戳，用于识别静默断网 */
  private lastFrameAt = 0
  private backoff = MIN_BACKOFF
  private authFailureHandler: (() => void) | null = null

  setAuthFailureHandler(handler: (() => void) | null) {
    this.authFailureHandler = handler
  }

  getState(): WSState {
    return this.state
  }

  subscribeFrames(listener: FrameListener): () => void {
    this.frameListeners.add(listener)
    return () => this.frameListeners.delete(listener)
  }

  subscribeState(listener: StateListener): () => void {
    this.stateListeners.add(listener)
    listener(this.state)
    return () => this.stateListeners.delete(listener)
  }

  connect() {
    if (!getToken()) return
    if (this.state === 'connecting' || this.state === 'identifying' || this.state === 'ready') return
    this.clearReconnect()
    this.closeTask()

    this.setState('connecting')
    Taro.connectSocket({ url: wsUrl() })
      .then((task) => {
        this.task = task
        task.onOpen(() => {
          // 已被新连接替换的旧 task 不能影响当前状态
          if (this.task !== task) return
          this.backoff = MIN_BACKOFF
          this.startReadyTimer(task)
        })
        task.onMessage(({ data }) => {
          if (this.task !== task) return
          let frame: WSServerMessage
          try {
            frame = JSON.parse(String(data)) as WSServerMessage
          } catch {
            return
          }
          this.handleFrame(frame)
        })
        task.onClose(({ code }) => {
          if (this.task !== task) return
          this.task = null
          this.clearReadyTimer()
          this.clearWatchdog()
          this.setState('disconnected')
          if (AUTH_FAILURE_CODES.has(code)) {
            this.outbox = []
            this.authFailureHandler?.()
            return
          }
          if (code === BUSY_CODE) this.backoff = Math.max(this.backoff, 8000)
          this.scheduleReconnect()
        })
        task.onError(() => {
          if (this.task !== task) return
          this.task = null
          this.clearReadyTimer()
          this.clearWatchdog()
          this.setState('disconnected')
          this.scheduleReconnect()
        })
      })
      .catch(() => {
        this.setState('disconnected')
        this.scheduleReconnect()
      })
  }

  disconnect() {
    this.clearReconnect()
    this.outbox = []
    this.closeTask()
    this.setState('disconnected')
  }

  send(message: WSClientMessage) {
    if (this.task && this.state === 'ready') {
      try {
        this.task.send({ data: JSON.stringify(message) })
      } catch {
        this.enqueue(message)
      }
      return
    }
    this.enqueue(message)
  }

  private enqueue(message: WSClientMessage) {
    if (this.outbox.length >= MAX_OUTBOX) this.outbox.shift()
    this.outbox.push(message)
    this.connect()
  }

  private handleFrame(frame: WSServerMessage) {
    this.lastFrameAt = Date.now()
    if (frame.type === 'hello') {
      this.setState('identifying')
      this.task?.send({
        data: JSON.stringify({
          type: 'identify',
          token: getToken(),
          protocol_version: PROTOCOL_VERSION,
        }),
      })
      return
    }
    if (frame.type === 'ready') {
      this.clearReadyTimer()
      this.lastFrameAt = Date.now()
      this.startWatchdog()
      this.setState('ready')
      const pending = this.outbox.splice(0)
      for (const message of pending) {
        this.task?.send({ data: JSON.stringify(message) })
      }
    }
    if (frame.type === 'ping') {
      this.task?.send({ data: JSON.stringify({ type: 'pong' }) })
      return
    }
    for (const listener of this.frameListeners) {
      listener(frame)
    }
  }

  private startReadyTimer(task: Taro.SocketTask) {
    this.clearReadyTimer()
    // 服务端未下发 ready（鉴权未完成/代理异常）时主动重连，避免永久卡在 identifying
    this.readyTimer = setTimeout(() => {
      this.readyTimer = null
      if (this.task !== task || this.state === 'ready') return
      this.closeTask()
      this.setState('disconnected')
      this.scheduleReconnect()
    }, 12000)
  }

  private clearReadyTimer() {
    if (this.readyTimer) {
      clearTimeout(this.readyTimer)
      this.readyTimer = null
    }
  }

  /**
   * 静默断网看门狗：网络黑洞时 WS 不会触发 close/error，
   * 只能靠「长时间收不到任何帧」判定链路失效并主动重连。
   */
  private startWatchdog() {
    this.clearWatchdog()
    this.watchdogTimer = setInterval(() => {
      if (this.state !== 'ready') return
      if (Date.now() - this.lastFrameAt < FRAME_STALE_TIMEOUT) return
      this.clearWatchdog()
      this.closeTask()
      this.setState('disconnected')
      this.scheduleReconnect()
    }, WATCHDOG_INTERVAL)
  }

  private clearWatchdog() {
    if (this.watchdogTimer) {
      clearInterval(this.watchdogTimer)
      this.watchdogTimer = null
    }
  }

  private scheduleReconnect() {
    if (this.reconnectTimer || !getToken()) return
    const delay = this.backoff
    this.backoff = Math.min(this.backoff * 2, MAX_BACKOFF)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null
      this.connect()
    }, delay)
  }

  private clearReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer)
      this.reconnectTimer = null
    }
  }

  private closeTask() {
    this.clearReadyTimer()
    this.clearWatchdog()
    if (this.task) {
      try {
        this.task.close({})
      } catch {
        // 连接已关闭时忽略
      }
      this.task = null
    }
  }

  private setState(state: WSState) {
    if (this.state === state) return
    this.state = state
    for (const listener of this.stateListeners) listener(state)
  }
}

export const socket = new AppSocket()
