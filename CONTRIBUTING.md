# Contributing

感谢你愿意帮助改进 QQ Codex Companion。

## 先确认是否符合项目方向

这个项目优先接受能够改善以下目标的改动：

- Windows 首次部署更简单；
- NapCat、OneBot 和 Codex 长期运行更稳定；
- 一对一私人陪伴更自然；
- 权限、数据和记忆更透明、更可控。

群管理、好友审批、QQ 空间、通用电脑控制和大型 WebUI 暂不属于近期范围。较大的功能请先提交 Feature Request，避免完成后才发现方向不一致。

## 本地开发

```powershell
git clone https://github.com/need-to-find-name/qq-codex-companion.git
Set-Location .\qq-codex-companion
uv sync
```

不要把真实 QQ、Token、代理、聊天记录、人物参考图或本机绝对路径写入公开文件。使用 `config.example.toml` 中的示例值编写测试和文档。

## 提交前检查

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src tests
uv build
& .\scripts\build-release.ps1
git diff --check
```

涉及真实 QQ、NapCat、天气或 Codex 模型的改动，还应说明完成了哪些本机集成测试。不要用“单元测试通过”代替真实消息链路验证。

## Pull Request 原则

- 一次 PR 聚焦一个主题；
- 解释用户能感受到的变化，而不只描述代码结构；
- 修复 Bug 时尽量增加回归测试；
- 修改配置时同步更新 `config.example.toml` 和 README；
- 修改用户可见行为时同步更新 `CHANGELOG.md`；
- 不降低默认的白名单、回环监听、只读沙箱或隐私保护；
- 不提交由工具自动生成的大型个人文件。

## 报告问题

请使用仓库提供的 Issue 模板，并尽量附上：

- Windows、QQ、NapCat、Codex 和项目版本；
- `qq-codex-doctor` 结果；
- 去除 QQ 号、Token、私人消息和本机用户名后的相关日志；
- 能否稳定复现，以及最短复现步骤。

安全问题不要发布到公开 Issue。请使用 GitHub Private Vulnerability Reporting；细节见 [`SECURITY.md`](SECURITY.md)。
