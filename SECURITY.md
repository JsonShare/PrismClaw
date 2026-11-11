# 安全策略

## 支持的版本

| 版本 | 安全更新 |
|------|----------|
| 1.x.x | 支持 |

## 报告漏洞

请通过 [GitHub Issues](https://github.com/JsonShare/PrismClaw/issues/new) 提交，标题以 `[SECURITY]` 开头，并尽量包含：

- 受影响的版本 / 部署形态
- 漏洞类型与影响范围
- 复现步骤或最小 PoC
- 你建议的修复方式（可选）

涉及未修复漏洞的利用细节时，请先只描述影响范围，等我们确认后再补充复现步骤。

我们通常会在 3 个工作日内确认收到，并在修复发布后致谢。

## 密钥与配置

- 仓库只提交脱敏模板（`backend/configs/*.example.yaml`、`deploy/.env.example`），真实配置由 `.gitignore` 拦截。
- 新增配置项时，请只修改对应的 `*.example.yaml`，**不要**写入真实密钥、内部地址或账号密码。
- 如果怀疑密钥已经泄露：立即轮换（JWT、数据库、模型 API Key、SMTP），并检查 git 历史与部署环境变量。

## 部署安全基线

- 首次登录后立即修改默认管理员密码。
- 生产环境必须替换 `channels.web.jwt_secret`，并使用强随机值。
- 保持沙箱开启（`bwrap`），不要在公网环境将 `enforce` 设为 `false`。
- Web 端不直接渲染未净化的 HTML；Agent 产出的富内容在 sandbox iframe 中执行。
- 不要把 `deploy/.env`、`backend/configs/*.yaml`、数据库备份上传到公共位置。
