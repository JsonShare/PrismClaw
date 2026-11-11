/**
 * 实时语音识别客户端（PCM 流式 + AudioWorklet）。
 *
 * AudioWorklet 在音频线程采集，按 ~100ms 聚合一帧，重采样到 16 kHz、
 * 转 int16 小端后以二进制帧发给 WS /api/speech/stream。
 * 相比已废弃的 ScriptProcessorNode，不占用主线程，录音期间 UI 不掉帧。
 */

export interface StreamingAsrHandlers {
  /** 增量结果（会不断被改写，直接覆盖上屏即可） */
  onPartial?: (text: string) => void
  /** 最终结果 */
  onFinal?: (text: string) => void
  /** 服务端就绪，可以开始说话 */
  onReady?: () => void
  /** 会话结束但没拿到 final（停止过早 / 上游中断），用于复位 UI */
  onClosed?: () => void
  onError?: (message: string) => void
}

const TARGET_SAMPLE_RATE = 16000
const CHUNK_MS = 100
const CONNECT_TIMEOUT_MS = 8000
const FINAL_TIMEOUT_MS = 4000
const WORKLET_FLUSH_TIMEOUT_MS = 50

/**
 * AudioWorklet 处理器源码。
 *
 * 用 Blob URL 注入，避免额外产物文件与部署路径（public/base）耦合。
 * 逐帧累积到 CHUNK_MS，再线性插值降到 16 kHz 并转 Int16，用 transferable
 * 传回主线程（零拷贝）。
 */
const PCM_WORKLET_SOURCE = `
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.targetRate = opts.targetRate || 16000;
    const chunkMs = opts.chunkMs || 100;
    this.need = Math.max(128, Math.ceil((sampleRate * chunkMs) / 1000 / 128) * 128);
    this.buffers = [];
    this.length = 0;
    this.port.onmessage = (event) => {
      if (event.data && event.data.type === 'flush') this.flush(true);
    };
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (!channel) return true;
    this.buffers.push(new Float32Array(channel));
    this.length += channel.length;
    if (this.length >= this.need) this.flush(false);
    return true;
  }
  flush(force) {
    if (this.length === 0 && !force) return;
    const merged = new Float32Array(this.length);
    let offset = 0;
    for (let i = 0; i < this.buffers.length; i += 1) {
      merged.set(this.buffers[i], offset);
      offset += this.buffers[i].length;
    }
    this.buffers = [];
    this.length = 0;
    const ratio = sampleRate / this.targetRate;
    const outLength = Math.floor(merged.length / ratio);
    const out = new Int16Array(Math.max(0, outLength));
    for (let i = 0; i < outLength; i += 1) {
      const pos = i * ratio;
      const idx = Math.floor(pos);
      const next = Math.min(idx + 1, merged.length - 1);
      const frac = pos - idx;
      let s = merged[idx] * (1 - frac) + merged[next] * frac;
      s = s < -1 ? -1 : s > 1 ? 1 : s;
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage(out.buffer, [out.buffer]);
  }
}
registerProcessor('pcm-capture', PcmCaptureProcessor);
`

let cachedWorkletUrl: string | null = null

function workletModuleUrl(): string {
  if (!cachedWorkletUrl) {
    cachedWorkletUrl = URL.createObjectURL(
      new Blob([PCM_WORKLET_SOURCE], { type: 'application/javascript' })
    )
  }
  return cachedWorkletUrl
}

/** 把 WS 关闭码翻译成可读原因（与服务端 speech.py 的约定对应）。 */
function closeReason(code: number, reason: string): string {
  if (reason) return reason
  switch (code) {
    case 4001:
      return '语音识别连接超时'
    case 4003:
      return '登录已失效，请重新登录'
    case 4100:
      return '服务端未配置流式语音识别'
    case 4101:
      return '流式语音识别服务不可达'
    default:
      return `语音识别连接已断开（${code}）`
  }
}

export class StreamingAsrSession {
  private ws: WebSocket | null = null
  private ctx: AudioContext | null = null
  private mediaStream: MediaStream | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private workletNode: AudioWorkletNode | null = null
  private sink: GainNode | null = null
  private handlers: StreamingAsrHandlers = {}
  private ready = false
  private finishing = false
  private closed = false
  private finalDelivered = false
  private closedByUser = false
  private finalTimer: number | null = null
  private resolveReady: ((ok: boolean, reason?: string) => void) | null = null

  /** 是否由用户主动停止/中断（用于抑制“已关闭”提示） */
  get userClosed(): boolean {
    return this.closedByUser
  }

  static isSupported(): boolean {
    if (typeof window === 'undefined') return false
    return (
      typeof WebSocket !== 'undefined' &&
      typeof AudioContext !== 'undefined' &&
      typeof AudioWorkletNode !== 'undefined' &&
      !!navigator.mediaDevices?.getUserMedia
    )
  }

  /** 建立连接并开始采集。resolve 代表服务端已就绪。 */
  async start(token: string, handlers: StreamingAsrHandlers): Promise<void> {
    this.handlers = handlers
    if (!token) throw new Error('未登录')
    if (!StreamingAsrSession.isSupported()) {
      throw new Error('当前浏览器不支持实时语音识别（需 AudioWorklet，Chrome 66+ / Safari 14.1+）')
    }

    await this.connect(token)
    await this.startCapture()
    // 采集真正开始后再置为“录音中”，避免权限弹窗期间按钮先变成录音态；
    // 若期间用户已点停止，则不再上报就绪。
    if (!this.closed && !this.finishing) this.handlers.onReady?.()
  }

  private async connect(token: string): Promise<void> {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${proto}//${window.location.host}/api/speech/stream`
    const ws = new WebSocket(url)
    ws.binaryType = 'arraybuffer'
    this.ws = ws

    await new Promise<void>((resolve, reject) => {
      const timer = window.setTimeout(() => {
        reject(new Error('连接语音识别服务超时'))
        this.abort()
      }, CONNECT_TIMEOUT_MS)

      const settle = (ok: boolean, reason?: string) => {
        window.clearTimeout(timer)
        const cb = this.resolveReady
        this.resolveReady = null
        if (cb) cb(ok, reason)
        if (ok) resolve()
        else reject(new Error(reason || '语音识别服务不可用'))
      }
      this.resolveReady = settle

      ws.onopen = () => {
        ws.send(JSON.stringify({ type: 'identify', token }))
      }

      ws.onmessage = (ev) => {
        if (typeof ev.data !== 'string') return
        let msg: { type?: string; text?: string; message?: string } | null = null
        try {
          msg = JSON.parse(ev.data) as { type?: string; text?: string; message?: string }
        } catch {
          return
        }
        if (!msg) return

        if (msg.type === 'ready') {
          this.ready = true
          settle(true)
        } else if (msg.type === 'partial') {
          this.handlers.onPartial?.(msg.text || '')
        } else if (msg.type === 'final') {
          this.finalDelivered = true
          this.handlers.onFinal?.(msg.text || '')
          this.cleanup()
        } else if (msg.type === 'error') {
          if (!this.ready) settle(false, msg.message)
          else this.handlers.onError?.(msg.message || '语音识别出错')
        }
      }

      ws.onerror = () => {
        if (!this.ready) settle(false, '无法连接语音识别服务')
      }

      ws.onclose = (ev) => {
        // 主动 cleanup（已拿到 final 或用户中断）触发的关闭不再上报
        if (this.closed) return
        if (!this.ready) {
          settle(false, closeReason(ev.code, ev.reason))
          return
        }
        if (!this.finishing) {
          this.handlers.onError?.(closeReason(ev.code, ev.reason))
        }
        this.cleanup()
      }
    })
  }

  private async startCapture(): Promise<void> {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    // 权限弹窗期间用户可能已点停止：此时不要再挂采集链路，直接释放麦克风
    if (this.closed || this.finishing) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    this.mediaStream = stream

    const ctx = new AudioContext()
    this.ctx = ctx
    if (ctx.state === 'suspended') {
      try {
        await ctx.resume()
      } catch {
        // 无用户手势时可能拒绝恢复；采集仍然可用
      }
    }
    if (this.closed || this.finishing) {
      this.releaseGraph()
      return
    }

    await ctx.audioWorklet.addModule(workletModuleUrl())
    if (this.closed || this.finishing) {
      this.releaseGraph()
      return
    }

    const source = ctx.createMediaStreamSource(stream)
    const node = new AudioWorkletNode(ctx, 'pcm-capture', {
      processorOptions: { targetRate: TARGET_SAMPLE_RATE, chunkMs: CHUNK_MS },
    })
    node.port.onmessage = (event) => {
      // finishing 期间仍需把最后一段尾部音频发出去；只有彻底关闭才丢
      if (this.closed) return
      const payload = event.data as ArrayBuffer
      if (payload && this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(payload)
      }
    }

    // 必须连到 destination 才会被持续拉取；0 增益避免回放麦克风
    const sink = ctx.createGain()
    sink.gain.value = 0
    source.connect(node)
    node.connect(sink)
    sink.connect(ctx.destination)

    this.source = source
    this.workletNode = node
    this.sink = sink
  }

  /** 停止采集并请求最终结果；服务端回 final 后自动清理。 */
  stop(): void {
    if (this.finishing || this.closed) return
    this.closedByUser = true
    this.finishing = true

    // 先停麦克风，再把 worklet 里不足一帧（<100ms）的尾部音刷出来，
    // 刷完之后才发 finish，避免尾音被丢或晚于 finish 到达。
    this.stopMicTracks()
    const node = this.workletNode
    if (!node) {
      this.sendFinish()
      return
    }

    let done = false
    const finalize = () => {
      if (done) return
      done = true
      node.port.onmessage = null
      this.workletNode = null
      this.releaseGraph()
      this.sendFinish()
    }

    node.port.onmessage = (event) => {
      const payload = event.data as ArrayBuffer
      if (payload && this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(payload)
      }
      finalize()
    }
    try {
      node.port.postMessage({ type: 'flush' })
    } catch {
      finalize()
      return
    }
    // 兜底：音频线程没及时回帧也要继续收尾
    window.setTimeout(finalize, WORKLET_FLUSH_TIMEOUT_MS)
  }

  /** 立即中断，不等待结果。 */
  abort(): void {
    this.closedByUser = true
    this.cleanup()
  }

  private sendFinish(): void {
    if (this.closed) return
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'finish' }))
      this.finalTimer = window.setTimeout(() => this.cleanup(), FINAL_TIMEOUT_MS)
      return
    }
    this.cleanup()
  }

  private stopMicTracks(): void {
    if (this.mediaStream) {
      this.mediaStream.getTracks().forEach((track) => track.stop())
      this.mediaStream = null
    }
  }

  /** 断开采集链路并关闭 AudioContext，但不动 WS。 */
  private releaseGraph(): void {
    if (this.workletNode) {
      const node = this.workletNode
      this.workletNode = null
      node.port.onmessage = null
      try {
        node.disconnect()
      } catch {
        // ignore
      }
    }
    if (this.source) {
      try {
        this.source.disconnect()
      } catch {
        // ignore
      }
      this.source = null
    }
    if (this.sink) {
      try {
        this.sink.disconnect()
      } catch {
        // ignore
      }
      this.sink = null
    }
    this.stopMicTracks()
    if (this.ctx) {
      const ctx = this.ctx
      this.ctx = null
      void ctx.close().catch(() => undefined)
    }
  }

  private cleanup(): void {
    if (this.closed) return
    this.closed = true
    if (this.finalTimer !== null) {
      window.clearTimeout(this.finalTimer)
      this.finalTimer = null
    }
    this.releaseGraph()
    if (this.ws) {
      const ws = this.ws
      this.ws = null
      try {
        if (ws.readyState === WebSocket.OPEN) ws.close()
      } catch {
        // ignore
      }
    }
    const cb = this.resolveReady
    this.resolveReady = null
    if (cb) cb(false, '语音识别已关闭')
    // 已就绪但没拿到 final：通知调用方复位 UI，避免转写态永久卡住
    if (this.ready && !this.finalDelivered) {
      this.handlers.onClosed?.()
    }
  }
}
