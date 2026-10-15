# Changelog

本项目的重要变更都会记录在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

暂无。

## [0.1.0] - 2026-10-15

### Added

- NapCat / OneBot 11 反向 WebSocket 到 Codex app-server 的本机桥接。
- 白名单 QQ 私聊、自然分条回复和连续消息合并。
- 图片与 GIF 输入、生成图片回传。
- 私聊戳一戳、概率戳回和冷却控制。
- 限时段、限频率的主动消息、生活图片与自拍风格图片。
- 每日天气提醒和基于明确表达的地点别名切换。
- Windows 状态检查、重启和登录自启动脚本。
- `qq-codex-doctor` 本地部署诊断命令。
- Windows GitHub Actions 持续集成和自动化测试。
- 可直接部署的 Windows Release ZIP、SHA-256 校验文件与标签发布流程。
- 贡献指南、Bug/功能 Issue 表单和 Pull Request 检查清单。
- 默认 `read-only` 沙箱、`never` 审批和关闭的持久记忆。

### Security

- OneBot 默认只监听回环地址。
- 账号、Token、日志、运行状态、收到的图片和角色参考图默认不进入 Git。

[Unreleased]: https://github.com/need-to-find-name/qq-codex-companion/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/need-to-find-name/qq-codex-companion/releases/tag/v0.1.0
