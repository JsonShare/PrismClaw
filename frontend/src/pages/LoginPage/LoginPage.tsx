import { useEffect, useState, type FormEvent } from 'react'
import { useAuth } from '../../context/AuthContext'
import {
  ArrowRight,
  Loader2,
  ChevronLeft,
  Activity,
  Scale,
  FileText,
  Compass,
} from 'lucide-react'
import { TextField } from '../../components/ui/TextField'
import ThemeToggle from '../../components/ui/ThemeToggle'
import logoPng from '../../images/logo/logo_192x192.png'
import { useUiStore } from '../../stores'
import { apiUrl } from '../../lib/system-adapter'

type View = 'login' | 'register' | 'reset'

const TITLES: Record<View, { title: string; subtitle: string }> = {
  login: { title: '欢迎使用', subtitle: '登录您的账号，开始智能工作之旅' },
  register: { title: '创建账号', subtitle: '注册后可使用昵称或邮箱登录' },
  reset: { title: '重置密码', subtitle: '通过邮箱验证码设置新密码' },
}

export default function Login() {
  const { login, loginError, loading, sendEmailCode, register, resetPassword } = useAuth()
  const theme = useUiStore((s) => s.theme)
  const setTheme = useUiStore((s) => s.setTheme)

  const [view, setView] = useState<View>('login')
  const [uid, setUid] = useState('')
  const [pwd, setPwd] = useState('')
  const [nickname, setNickname] = useState('')
  const [email, setEmail] = useState('')
  const [code, setCode] = useState('')
  const [newPwd, setNewPwd] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [emailEnabled, setEmailEnabled] = useState<boolean | null>(null)
  const [sending, setSending] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [countdown, setCountdown] = useState(0)

  useEffect(() => {
    let active = true
    fetch(apiUrl('/api/settings'))
      .then((res) => (res.ok ? res.json() : null))
      .then((data: { email_verification_enabled?: unknown } | null) => {
        if (active) setEmailEnabled(data ? Boolean(data.email_verification_enabled) : false)
      })
      .catch(() => {
        // 网络异常时保持未知状态（不误报“未配置”），由发送请求返回真实错误。
      })
    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    if (countdown <= 0) return
    const timer = setTimeout(() => setCountdown((n) => (n > 0 ? n - 1 : 0)), 1000)
    return () => clearTimeout(timer)
  }, [countdown])

  const switchView = (next: View) => {
    setView(next)
    setFormError(null)
    setNotice(null)
    setCode('')
    setNewPwd('')
    setCountdown(0)
  }

  const submitLogin = async (e: FormEvent) => {
    e.preventDefault()
    if (!uid.trim()) return
    setFormError(null)
    setNotice(null)
    try {
      await login(uid.trim(), pwd)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '登录失败，请稍后重试')
    }
  }

  const sendCode = async () => {
    if (!email.trim()) {
      setFormError('请先填写邮箱')
      return
    }
    setFormError(null)
    setNotice(null)
    setSending(true)
    try {
      await sendEmailCode(email.trim(), view === 'reset' ? 'reset' : 'register')
      setCountdown(60)
      setNotice('验证码已发送，请查收邮箱（5 分钟内有效）')
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '验证码发送失败，请稍后重试')
    } finally {
      setSending(false)
    }
  }

  const submitRegister = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    setNotice(null)
    setSubmitting(true)
    try {
      await register({
        nickname: nickname.trim(),
        email: email.trim(),
        code: code.trim(),
        password: newPwd,
      })
      const registeredNickname = nickname.trim()
      switchView('login')
      setUid(registeredNickname)
      setNotice('注册成功，请使用昵称或邮箱登录')
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '注册失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  const submitReset = async (e: FormEvent) => {
    e.preventDefault()
    setFormError(null)
    setNotice(null)
    setSubmitting(true)
    try {
      await resetPassword({
        email: email.trim(),
        code: code.trim(),
        newPassword: newPwd,
      })
      switchView('login')
      setUid(email.trim())
      setPwd('')
      setNotice('密码已重置，请使用新密码登录')
    } catch (err) {
      setFormError(err instanceof Error ? err.message : '重置密码失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  const errorText = view === 'login' ? formError || loginError : formError
  const emailDisabled = emailEnabled === false
  const emailDisabledNotice = emailDisabled && (
    <div className="login-alert is-static login-alert-warning">
      邮箱服务未配置，暂时无法发送验证码，请联系管理员。
    </div>
  )
  const codeRow = (
    <div className="login-code-row">
      <TextField
        inputMode="numeric"
        maxLength={6}
        placeholder="6 位验证码"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        className="h-12"
      />
      <button
        type="button"
        className="login-code-btn"
        onClick={sendCode}
        disabled={sending || countdown > 0 || emailDisabled}
      >
        {countdown > 0 ? `${countdown}s` : sending ? '发送中' : '获取验证码'}
      </button>
    </div>
  )

  return (
    <div className="login-bg">
      {/* ══ 左面板：品牌 + 介绍 + 四领域卡 ══ */}
      <div className="login-left">
        <div className="login-aurora" aria-hidden="true">
          <div className="aurora orb orb-1" />
          <div className="aurora orb orb-2" />
          <div className="aurora orb orb-3" />
        </div>
        <div className="login-left-inner">
          <div className="brand-row">
            <img src={logoPng} alt="PrismClaw" className="brand-logo" />
            <div className="brand-text">
              <span className="brand-name">PrismClaw</span>
              <span className="brand-tagline">一个7×24小时帮干活的全场景AI助手</span>
            </div>
          </div>

          <p className="login-hero">
            生产级多域 AI 智能体平台 —— 记得住团队每一份经验，<b>用得越久越懂你</b>；敏感操作层层审批、全程可追溯，<b>安全无需你操心</b>。一套系统，从三大场景到星辰大海。
          </p>

          <div className="domain-grid">
            <div className="domain-card domain-medical login-domain-card">
              <Activity className="d-icon" strokeWidth={1.8} />
              <h3>医疗 AI</h3>
              <p>融合权威医学指南与循证文献库，构建专业医学知识底座。辅助医生诊疗决策，提供有循证依据的临床支持。</p>
            </div>
            <div className="domain-card domain-legal login-domain-card">
              <Scale className="d-icon" strokeWidth={1.8} />
              <h3>法律 AI</h3>
              <p>覆盖法规检索、合同审查与案例分析全链路，提供精准法律意见与合规审查，构建完整法律技能生态。</p>
            </div>
            <div className="domain-card domain-office login-domain-card">
              <FileText className="d-icon" strokeWidth={1.8} />
              <h3>办公 AI</h3>
              <p>智能文档处理、会议摘要生成与工作流自动化，全面提升协作效率。支持 IM 对话与多模态交互。</p>
            </div>
            <div className="domain-card domain-base login-domain-card">
              <Compass className="d-icon" strokeWidth={1.8} />
              <h3>更多领域，值得探索</h3>
              <p>医疗、法律、办公只是起点。同一套垂直大模型基座与技能体系，可按行业快速扩展——金融、制造、政务……下一个领域，由你定义。</p>
            </div>
          </div>
        </div>
      </div>

      {/* ══ 右面板：登录 / 注册 / 找回密码 ══ */}
      <div className="login-right">
        <div className="login-toggle">
          <ThemeToggle theme={theme} onChange={setTheme} />
        </div>

        <div className="login-form-wrap login-form-card">
          <h2>{TITLES[view].title}</h2>
          <p>{TITLES[view].subtitle}</p>

          {view === 'login' && (
            <form onSubmit={submitLogin}>
              <div className="login-fields">
                <TextField
                  type="text"
                  placeholder="请输入昵称或邮箱"
                  value={uid}
                  onChange={(e) => setUid(e.target.value)}
                  autoFocus
                  className="h-12"
                />
                <TextField
                  type="password"
                  placeholder="请输入密码"
                  value={pwd}
                  onChange={(e) => setPwd(e.target.value)}
                  className="h-12"
                />
              </div>

              <button type="submit" className="login-submit" disabled={loading || !uid.trim()}>
                {loading ? (
                  <Loader2 size={16} className="animate-spin" />
                ) : (
                  <>
                    登录
                    <ArrowRight size={15} />
                  </>
                )}
              </button>

              <div className="login-links">
                <button type="button" className="login-link" onClick={() => switchView('reset')}>
                  忘记密码？
                </button>
                <button type="button" className="login-link" onClick={() => switchView('register')}>
                  新用户注册
                </button>
              </div>
            </form>
          )}

          {view === 'register' && (
            <form onSubmit={submitRegister}>
              <div className="login-fields">
                {emailDisabledNotice}
                <TextField
                  type="text"
                  placeholder="昵称（2-20 位中文、字母、数字或下划线）"
                  value={nickname}
                  onChange={(e) => setNickname(e.target.value)}
                  autoFocus
                  className="h-12"
                />
                <TextField
                  type="email"
                  placeholder="邮箱"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="h-12"
                />
                {codeRow}
                <TextField
                  type="password"
                  placeholder="密码（8-32 位，含字母和数字）"
                  value={newPwd}
                  onChange={(e) => setNewPwd(e.target.value)}
                  className="h-12"
                />
              </div>

              <button
                type="submit"
                className="login-submit"
                disabled={
                  submitting ||
                  emailDisabled ||
                  !nickname.trim() ||
                  !email.trim() ||
                  !code.trim() ||
                  !newPwd
                }
              >
                {submitting ? <Loader2 size={16} className="animate-spin" /> : '注册'}
              </button>

              <div className="login-switch">
                <button type="button" className="login-link" onClick={() => switchView('login')}>
                  <ChevronLeft size={13} />
                  返回登录
                </button>
              </div>
            </form>
          )}

          {view === 'reset' && (
            <form onSubmit={submitReset}>
              <div className="login-fields">
                {emailDisabledNotice}
                <TextField
                  type="email"
                  placeholder="邮箱"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  autoFocus
                  className="h-12"
                />
                {codeRow}
                <TextField
                  type="password"
                  placeholder="新密码（8-32 位，含字母和数字）"
                  value={newPwd}
                  onChange={(e) => setNewPwd(e.target.value)}
                  className="h-12"
                />
              </div>

              <button
                type="submit"
                className="login-submit"
                disabled={submitting || emailDisabled || !email.trim() || !code.trim() || !newPwd}
              >
                {submitting ? <Loader2 size={16} className="animate-spin" /> : '重置密码'}
              </button>

              <div className="login-switch">
                <button type="button" className="login-link" onClick={() => switchView('login')}>
                  <ChevronLeft size={13} />
                  返回登录
                </button>
              </div>
            </form>
          )}

          {errorText && <div className="login-alert login-alert-error">{errorText}</div>}
          {notice && <div className="login-alert login-alert-info">{notice}</div>}
        </div>
      </div>
    </div>
  )
}
