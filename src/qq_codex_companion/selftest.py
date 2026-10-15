from __future__ import annotations

import asyncio
import os
from pathlib import Path

from .codex_client import CodexAppServer
from .config import load_settings
from .replies import parse_reply
from .state import ThreadState


async def _run() -> None:
    config_path = Path(os.environ.get("QQ_CODEX_CONFIG", "config.local.toml"))
    settings = load_settings(config_path)
    state = ThreadState(settings.runtime.state_file.with_name("selftest-state.json"))
    client = CodexAppServer(settings.codex, state)
    image_paths = [
        Path(value).resolve()
        for value in os.environ.get("QQ_CODEX_SELFTEST_IMAGES", "").split(os.pathsep)
        if value.strip()
    ]
    interaction = os.environ.get("QQ_CODEX_SELFTEST_INTERACTION", "message").strip()
    if interaction not in {"message", "proactive_image", "proactive_selfie"}:
        raise ValueError(
            "QQ_CODEX_SELFTEST_INTERACTION must be message, proactive_image, or proactive_selfie"
        )
    if (
        interaction == "proactive_selfie"
        and not image_paths
        and settings.proactive.selfie_reference_path is not None
    ):
        image_paths = [settings.proactive.selfie_reference_path]
    await client.start()
    try:
        prompt = (
            "这是图片连接自检。请确认你能看到附件，并用两条自然、简短的中文消息概括图片内容或差异，不要解释测试机制。"
            if image_paths
            else "这是连接自检。请用两条自然、简短的中文消息回应，不要解释测试过程。"
        )
        result = await client.chat(
            "selftest:private",
            next(iter(settings.onebot.allowed_user_ids)),
            prompt,
            interaction=interaction,
            image_paths=image_paths,
        )
        parts = parse_reply(
            result.text,
            max_parts=settings.reply.max_parts,
            max_chars=settings.reply.max_chars_per_part,
        )
        print(f"Codex turn: OK; natural message parts: {len(parts)}")
        for index, part in enumerate(parts, start=1):
            print(f"  {index}. {part}")
        print(f"Generated images: {len(result.image_paths)}")
        for path in result.image_paths:
            print(f"  image: {path}")
    finally:
        await client.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
