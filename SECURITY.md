# Security Policy

QQ Codex Companion receives QQ messages and attachments, forwards trusted private-chat input to a local Codex process, and can send actions back through NapCat. Treat every incoming event and file as untrusted input.

## Supported versions

Security fixes are applied to the latest tagged release and the current `main` branch. Early `0.x` releases may receive fixes through a newer minor or patch release rather than a backport.

## Report a vulnerability privately

Do not open a public Issue containing an exploit, access token, QQ number, private message, local path, or other sensitive evidence.

Use [GitHub Private Vulnerability Reporting](https://github.com/need-to-find-name/qq-codex-companion/security/advisories/new). Include the affected version, impact, minimum reproduction, and a suggested mitigation when available. Redact unrelated personal data.

If Private Vulnerability Reporting is unavailable, open a public Issue that only says a private security contact is needed; do not include vulnerability details.

## Secure deployment defaults

- Keep the OneBot reverse WebSocket bound to `127.0.0.1` or `::1` unless you operate a separately authenticated tunnel.
- Use a strong, unique OneBot access token and never commit `config.local.toml`.
- Restrict `allowed_user_ids` to QQ accounts you control.
- Keep the Codex sandbox at `read-only` and approval policy at `never` for ordinary unattended conversation.
- Keep persistent memory disabled unless you have reviewed its local storage and deletion behavior.
- Review QQ, NapCat and Codex updates before deploying them on a computer containing sensitive data.
- Rotate the OneBot token immediately if it appears in a log, screenshot, Issue, commit, or chat message sent to an untrusted person.

The project cannot eliminate risks created by unofficial QQ integration, account enforcement, a compromised local computer, a malicious dependency, or a user intentionally widening the default sandbox and network boundaries.
