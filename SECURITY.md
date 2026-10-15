# Security

This project receives messages from QQ and forwards them to a local Codex process. Treat every incoming message and attachment as untrusted input.

- Keep the OneBot reverse WebSocket bound to loopback unless you have a separate authenticated tunnel.
- Use a strong, unique OneBot access token and never commit `config.local.toml`.
- Restrict `allowed_user_ids` to QQ accounts you control.
- Keep the Codex sandbox at `read-only` and approval policy at `never` for ordinary conversation.
- Review NapCat and QQ updates before deploying them on a machine containing sensitive data.

If you publish a fork, enable GitHub private vulnerability reporting or document another private reporting channel before accepting external users.
