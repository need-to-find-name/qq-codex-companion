# QQ Codex Companion

一个运行在 Windows 本机的 QQ 私聊桥接器：让 NapCat 通过 OneBot 11 反向 WebSocket 把消息交给 Codex，再将结构化回复发回 QQ。

项目不依赖 DeepSeek 或 AstrBot，不包含任何可直接使用的 QQ 账号、Token、聊天记录、个人画像或本机路径。

## 功能

- 仅响应白名单 QQ 私聊，其他账号不会进入 Codex。
- 支持自然分条回复，避免逐字拆分和固定多段输出。
- 支持图片与 GIF 接收、视觉理解，以及 Codex 生成图片回传。
- 支持私聊“戳一戳”和低概率自然戳回。
- 支持限时段、限频率的主动消息，可选低概率生活图片或自拍风格图片。
- 支持每天一次的简短天气提醒，以及通过自定义地点别名切换提醒位置。
- 支持本机状态检查、重启脚本和可选的 Windows 登录自启动守护。
- 默认使用 Codex `read-only` 沙箱和 `never` 审批策略。
- 持久个人记忆默认并明确关闭。

## 工作方式

```text
QQ 主账号
   ↕ 私聊
QQ Bot 账号 + NapCat
   ↕ OneBot 11 reverse WebSocket
qq-codex-companion
   ↕ stdio JSON-RPC
Codex app-server ↔ workspace/
```

桥接器必须先监听 OneBot 地址，NapCat 再作为反向 WebSocket 客户端连接。第一次登录 QQ 或登录态失效时，通常仍需人工扫码。

## 环境要求

- Windows 10/11
- QQ NT 与 NapCat
- 已安装并登录的 Codex Desktop 或可用的 `codex` 命令
- [uv](https://docs.astral.sh/uv/)
- Python 3.14（由 `.python-version` 与 uv 管理）

## 快速开始

### 1. 初始化

在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\initialize.ps1
```

脚本会安装 Python 依赖，并在缺失时生成两个不会进入 Git 的本地文件：

- `config.local.toml`：QQ、Token、代理和对话行为配置。
- `scripts/runtime.local.psd1`：NapCat 与 QQ 的本机安装路径。

### 2. 填写 `config.local.toml`

至少修改：

```toml
[onebot]
host = "127.0.0.1"
port = 8643
path = "/ws"
access_token = "换成随机长字符串"
bot_qq = 1000000001
allowed_user_ids = [1000000002]
```

- `bot_qq`：专门作为 Bot 登录的 QQ 小号。
- `allowed_user_ids`：允许与 Bot 对话的 QQ 号；建议只放自己的主账号。
- `access_token`：必须与 NapCat 的 OneBot Token 完全一致。

可用 PowerShell 生成随机 Token：

```powershell
$bytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
[BitConverter]::ToString($bytes).Replace('-', '')
```

如果 Codex 或天气接口需要本机代理，可分别设置：

```toml
[codex]
proxy_url = "http://127.0.0.1:7890"

[weather]
proxy_url = "http://127.0.0.1:7890"
```

不需要代理时保持空字符串。`codex.model = ""` 表示沿用当前 Codex 默认模型。

### 3. 配置 NapCat

在 NapCat 中新建 OneBot 11 反向 WebSocket 客户端：

- URL：`ws://127.0.0.1:8643/ws`
- Token：与 `config.local.toml` 的 `onebot.access_token` 相同
- 自动重连：开启

端口或路径可以修改，但两端必须一致。不要把 OneBot 服务直接暴露到公网。

### 4. 启动

先启动桥接器：

```powershell
uv run qq-codex-companion
```

再启动 NapCat / QQ，必要时扫码登录 Bot 账号。看到 NapCat 已连接反向 WebSocket 后，从白名单账号给 Bot 发送一条私聊消息。

## 可选功能

### 主动消息和图片

`[proactive]` 控制活跃时段、安静多久后尝试联系、每日上限、发送概率和图片概率。主动图片每日默认最多一张。

若要让自拍风格图片保持同一视觉角色，将本地参考图放进 `workspace/persona/`，然后只在 `config.local.toml` 中填写：

```toml
selfie_reference = "./workspace/persona/reference.png"
```

该目录中的图片默认被 Git 忽略。

### 天气提醒

开启 `[weather]` 并填写一个 Open-Meteo 可识别的地点：

```toml
[weather]
enabled = true
location = "北京市海淀区"
daily_time = "07:30"

[weather.location_aliases]
"家" = "北京市海淀区"
"学校" = "北京市朝阳区"
```

当用户明确说“我回家了”或“我到学校了”时，后续提醒会切换到相应地点；“明天可能回家”之类的不确定表达不会切换。

### Windows 登录自启动

先编辑 `scripts/runtime.local.psd1`：

```powershell
@{
    NapCatRoot = 'C:\Path\To\NapCat'
    QQPath = 'C:\Path\To\QQ.exe'
    OneBotWsPort = 8643
}
```

安装并启动用户级计划任务：

```powershell
& .\scripts\install-qqbot-autostart.ps1
```

查看状态、重启或卸载：

```powershell
& .\scripts\qqbot-status.ps1
& .\scripts\restart-qqbot-bridge.ps1
& .\scripts\uninstall-qqbot-autostart.ps1
```

守护脚本不会绕过 QQ 的扫码或风控，也不会关闭一个已打开但未由指定 NapCat 启动器注入的 QQ 进程。

## 隐私与安全

以下内容默认不会进入 Git：

- `config.local.toml`
- `scripts/runtime.local.psd1`
- `data/` 与 `logs/`
- `workspace/incoming-images/`
- `workspace/persona/` 中的本地参考图
- `workspace/memory/`
- `.codex-remote-attachments/`

持久个人记忆保持 `enabled = false`。普通 QQ 消息只使用当前 Codex thread 的上下文；如果希望每次都完全从零开始，可删除 `data/state.json` 及对应 Codex thread。

公开仓库前仍建议检查：

```powershell
git status --short --ignored
git grep -n -I -E "access_token|allowed_user_ids|bot_qq|proxy_url"
```

更多注意事项见 [SECURITY.md](SECURITY.md)。

## 测试

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests
```

主动图片生成自检只生成图片，不发送到 QQ：

```powershell
$env:QQ_CODEX_SELFTEST_INTERACTION = 'proactive_image'
uv run qq-codex-selftest
Remove-Item Env:QQ_CODEX_SELFTEST_INTERACTION
```

## 故障排查

- `ECONNREFUSED 127.0.0.1:8643`：桥接器尚未启动、端口不一致，或端口被其他程序占用。
- NapCat 已启动但未连接：核对反向 WebSocket URL、Token 和自动重连设置。
- Bot 长时间无回复：运行 `scripts\qqbot-status.ps1`，再查看 `logs\bridge.log`。
- Codex 请求失败：先在同一 Windows 用户下确认 Codex 已登录，再检查代理与账户使用限制。
- QQ 无法快速登录：重新扫码；本项目不会尝试绕过 QQ 的账号安全验证。

## 项目结构

```text
src/qq_codex_companion/   OneBot、Codex、天气和状态逻辑
skills/qq-companion/      QQ 陪伴式回复约束
workspace/                Codex 的只读对话工作区
scripts/                  初始化、状态检查和自启动脚本
tests/                    单元测试
config.example.toml       可公开的配置模板
```

## License

[MIT](LICENSE)
