import Taro from '@tarojs/taro'

import { apiOrigin, getToken } from './request'

const IS_H5 = process.env.TARO_ENV === 'h5'
const TARGET_RATE = 16000
/** 每帧 100ms（16kHz * 0.1s） */
const FRAME_SAMPLES = TARGET_RATE / 10
const CONNECT_TIMEOUT_MS = 8000
const FINAL_TIMEOUT_MS = 4000

export type VoiceState = 'connecting' | 'recording' | 'finishing'

export interface VoiceOptions {
  onPartial: (text: string) => void
  onFinal: (text: string) => void
  onError: (message: string) => void
  onStateChange?: (state: VoiceState) => void
}

export interface VoiceSession {
  stop: () => void
}

function speechUrl(): string {
  const origin = apiOrigin()
  if (origin) return `${origin.replace(/^http/, 'ws')}/api/speech/stream`
  const loc = window.location
  return `${loc.protocol === 'https:' ? 'wss:' : 'ws:'}//${loc.host}/api/speech/stream`
}

function closeReason(code: number): string {
  switch (code) {
    case 4001:
      return '语音识别连接超时，请重试'
    case 4003:
      return '登录状态已失效，请重新登录'
    case 4100:
      return '服务端未配置语音识别'
    case 4101:
    case 4102:
      return '语音识别服务暂时不可用'
    default:
      return '语音识别中断，请重试'
  }
}

/** AudioWorklet 内联源码：聚合 100ms、线性插值降到 16kHz、转 Int16LE 分帧回传 */
const PCM_WORKLET_SOURCE = `
class PCMCapture extends AudioWorkletProcessor {
  constructor() {
    super()
    this.buf = []
    this.pos = 0
    this.out = []
    this.ratio = sampleRate / ${TARGET_RATE}
    this.port.onmessage = (event) => {
      if (event.data === 'flush') {
        this.flush(true)
        this.port.postMessage({ done: true })
      }
    }
  }
  flush() {
    if (!this.out.length) return
    const pcm = new Int16Array(this.out.length)
    for (let i = 0; i < this.out.length; i += 1) {
      const v = Math.max(-1, Math.min(1, this.out[i]))
      pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff
    }
    this.out = []
    this.port.postMessage(pcm.buffer, [pcm.buffer])
  }
  process(inputs) {
    const input = inputs[0] && inputs[0][0]
    if (input && input.length) {
      for (let i = 0; i < input.length; i += 1) this.buf.push(input[i])
      while (this.pos < this.buf.length - 1) {
        const idx = Math.floor(this.pos)
        const frac = this.pos - idx
        this.out.push(this.buf[idx] * (1 - frac) + this.buf[idx + 1] * frac)
        this.pos += this.ratio
        if (this.out.length >= ${FRAME_SAMPLES}) this.flush()
      }
      const consumed = Math.floor(this.pos)
      if (consumed > 0) {
        this.buf = this.buf.slice(consumed)
        this.pos -= consumed
      }
    }
    return true
  }
}
registerProcessor('pcm-capture', PCMCapture)
`

interface H5Resources {
  ws: WebSocket
  ctx: AudioContext
  stream: MediaStream
  source: MediaStreamAudioSourceNode
  node: AudioWorkletNode
  sink: GainNode
}

function startH5(options: VoiceOptions): VoiceSession {
  let resources: H5Resources | null = null
  let closed = false
  let finalTimer: ReturnType<typeof setTimeout> | null = null
  let connectTimer: ReturnType<typeof setTimeout> | null = null
  let gotFinal = false

  const cleanup = () => {
    if (closed) return
    closed = true
    if (finalTimer) clearTimeout(finalTimer)
    if (connectTimer) clearTimeout(connectTimer)
    const current = resources
    resources = null
    if (!current) return
    try {
      current.source.disconnect()
      current.node.disconnect()
      current.sink.disconnect()
    } catch {
      // 音频图可能已被关闭，忽略
    }
    current.stream.getTracks().forEach((track) => track.stop())
    void current.ctx.close().catch(() => undefined)
    if (current.ws.readyState === WebSocket.OPEN) current.ws.close()
  }

  const fail = (message: string) => {
    cleanup()
    options.onError(message)
  }

  const sendFinish = () => {
    const current = resources
    if (!current || current.ws.readyState !== WebSocket.OPEN) {
      fail('没有识别到语音，请重试')
      return
    }
    if (finalTimer) clearTimeout(finalTimer)
    finalTimer = setTimeout(() => fail('没有识别到语音，请重试'), FINAL_TIMEOUT_MS)
    options.onStateChange?.('finishing')
    current.ws.send(JSON.stringify({ type: 'finish' }))
    // 让 worklet 把最后一帧吐出来再断开麦克风
    current.node.port.postMessage('flush')
    setTimeout(() => {
      current.stream.getTracks().forEach((track) => track.stop())
    }, 120)
  }

  options.onStateChange?.('connecting')

  const ws = new WebSocket(speechUrl())
  ws.binaryType = 'arraybuffer'
  connectTimer = setTimeout(() => fail('语音识别连接超时，请重试'), CONNECT_TIMEOUT_MS)

  ws.onopen = () => {
    ws.send(JSON.stringify({ type: 'identify', token: getToken() }))
  }

  ws.onmessage = (event) => {
    if (typeof event.data !== 'string') return
    let frame: { type?: string; text?: string; message?: string }
    try {
      frame = JSON.parse(event.data)
    } catch {
      return
    }
    if (frame.type === 'ready') {
      if (connectTimer) clearTimeout(connectTimer)
      options.onStateChange?.('recording')
      return
    }
    if (frame.type === 'partial' && typeof frame.text === 'string') {
      options.onPartial(frame.text)
      return
    }
    if (frame.type === 'final') {
      gotFinal = true
      if (finalTimer) clearTimeout(finalTimer)
      options.onFinal(typeof frame.text === 'string' ? frame.text : '')
      cleanup()
      return
    }
    if (frame.type === 'error') {
      fail(frame.message || '语音识别失败')
    }
  }

  ws.onerror = () => {
    if (gotFinal) return
    fail('语音识别连接失败')
  }

  ws.onclose = (event) => {
    if (closed || gotFinal) return
    fail(closeReason(event.code))
  }

  void (async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      let ctx: AudioContext
      try {
        ctx = new AudioContext({ sampleRate: TARGET_RATE })
      } catch {
        ctx = new AudioContext()
      }
      const workletUrl = URL.createObjectURL(
        new Blob([PCM_WORKLET_SOURCE], { type: 'application/javascript' }),
      )
      await ctx.audioWorklet.addModule(workletUrl)
      URL.revokeObjectURL(workletUrl)
      if (closed) {
        stream.getTracks().forEach((track) => track.stop())
        void ctx.close()
        return
      }
      const source = ctx.createMediaStreamSource(stream)
      const node = new AudioWorkletNode(ctx, 'pcm-capture')
      // 0 增益接到 destination，保证 worklet 持续被驱动且不产生回声
      const sink = ctx.createGain()
      sink.gain.value = 0
      source.connect(node)
      node.connect(sink)
      sink.connect(ctx.destination)
      node.port.onmessage = (event) => {
        if (closed || !(event.data instanceof ArrayBuffer)) return
        if (ws.readyState === WebSocket.OPEN) ws.send(event.data)
      }
      resources = { ws, ctx, stream, source, node, sink }
    } catch (error) {
      const name = (error as Error)?.name
      fail(name === 'NotAllowedError' ? '需要麦克风权限才能语音输入' : '无法启动录音')
    }
  })()

  return {
    stop: () => {
      if (closed) return
      sendFinish()
    },
  }
}

function startWeapp(options: VoiceOptions): VoiceSession {
  let closed = false
  let task: Taro.SocketTask | null = null
  let finalTimer: ReturnType<typeof setTimeout> | null = null
  const recorder = Taro.getRecorderManager()

  const cleanup = () => {
    if (closed) return
    closed = true
    if (finalTimer) clearTimeout(finalTimer)
    recorder.stop()
    if (task) {
      void task.close({})
      task = null
    }
  }

  const fail = (message: string) => {
    cleanup()
    options.onError(message)
  }

  recorder.onFrameRecorded(({ frameBuffer }) => {
    if (closed || !frameBuffer || !task) return
    task.send({ data: frameBuffer, fail: () => undefined })
  })
  recorder.onError(() => fail('无法启动录音'))
  recorder.onStop(() => {
    if (closed || !task) return
    task.send({ data: JSON.stringify({ type: 'finish' }), fail: () => undefined })
  })

  options.onStateChange?.('connecting')
  Taro.connectSocket({ url: speechUrl() })
    .then((socketTask) => {
      task = socketTask
      socketTask.onOpen(() => {
        socketTask.send({ data: JSON.stringify({ type: 'identify', token: getToken() }) })
      })
      socketTask.onMessage(({ data }) => {
        if (typeof data !== 'string') return
        let frame: { type?: string; text?: string; message?: string }
        try {
          frame = JSON.parse(data)
        } catch {
          return
        }
        if (frame.type === 'ready') {
          options.onStateChange?.('recording')
          recorder.start({
            format: 'PCM',
            sampleRate: TARGET_RATE,
            numberOfChannels: 1,
            frameSize: 4,
            duration: 60000,
          })
          return
        }
        if (frame.type === 'partial' && typeof frame.text === 'string') {
          options.onPartial(frame.text)
          return
        }
        if (frame.type === 'final') {
          if (finalTimer) clearTimeout(finalTimer)
          options.onFinal(typeof frame.text === 'string' ? frame.text : '')
          cleanup()
          return
        }
        if (frame.type === 'error') fail(frame.message || '语音识别失败')
      })
      socketTask.onClose(({ code }) => {
        if (!closed) fail(closeReason(code))
      })
      socketTask.onError(() => fail('语音识别连接失败'))
    })
    .catch(() => fail('语音识别连接失败'))

  return {
    stop: () => {
      if (closed) return
      options.onStateChange?.('finishing')
      finalTimer = setTimeout(() => fail('没有识别到语音，请重试'), FINAL_TIMEOUT_MS)
      recorder.stop()
    },
  }
}

export function startVoice(options: VoiceOptions): VoiceSession {
  return IS_H5 ? startH5(options) : startWeapp(options)
}
