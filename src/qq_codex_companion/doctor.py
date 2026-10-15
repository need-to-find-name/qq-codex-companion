from __future__ import annotations

import argparse
import ipaddress
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .codex_client import codex_subprocess_environment, resolve_codex_executable
from .config import Settings, load_settings


@dataclass(frozen=True)
class CheckResult:
    level: str
    name: str
    detail: str
    fix: str = ""


def _result(level: str, name: str, detail: str, fix: str = "") -> CheckResult:
    return CheckResult(level=level, name=name, detail=detail, fix=fix)


def _is_loopback(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def configuration_checks(settings: Settings) -> list[CheckResult]:
    results: list[CheckResult] = []

    if _is_loopback(settings.onebot.host):
        results.append(
            _result("PASS", "OneBot bind address", f"{settings.onebot.host} is loopback-only")
        )
    else:
        results.append(
            _result(
                "FAIL",
                "OneBot bind address",
                f"{settings.onebot.host} may expose the bridge outside this computer",
                "Set onebot.host to 127.0.0.1 unless you have an authenticated tunnel.",
            )
        )

    if settings.onebot.bot_qq <= 0:
        results.append(
            _result(
                "FAIL",
                "Bot QQ",
                "onebot.bot_qq is missing or invalid",
                "Set it to the QQ account currently logged in through NapCat.",
            )
        )
    elif settings.onebot.bot_qq in settings.onebot.allowed_user_ids:
        results.append(
            _result(
                "FAIL",
                "QQ allowlist",
                "The bot account is also in allowed_user_ids",
                "Keep the bot QQ and the trusted user QQ as two different accounts.",
            )
        )
    else:
        results.append(
            _result(
                "PASS",
                "QQ allowlist",
                f"1 bot account and {len(settings.onebot.allowed_user_ids)} trusted user(s)",
            )
        )

    token_length = len(settings.onebot.access_token)
    if token_length < 24:
        results.append(
            _result(
                "WARN",
                "OneBot token",
                f"Configured, but only {token_length} characters long",
                "Use a random token of at least 24 characters and copy it into NapCat.",
            )
        )
    else:
        results.append(_result("PASS", "OneBot token", "Configured and not displayed"))

    if settings.codex.sandbox == "read-only":
        results.append(_result("PASS", "Codex sandbox", "read-only"))
    else:
        results.append(
            _result(
                "WARN",
                "Codex sandbox",
                f"{settings.codex.sandbox} permits more than read-only conversation",
                "Use read-only unless QQ users intentionally need workspace write access.",
            )
        )

    if settings.codex.approval_policy == "never":
        results.append(_result("PASS", "Approval policy", "never (non-interactive)"))
    else:
        results.append(
            _result(
                "WARN",
                "Approval policy",
                f"{settings.codex.approval_policy} can block an unattended QQ turn",
                "Use never for the default read-only companion configuration.",
            )
        )

    if settings.memory.enabled:
        results.append(
            _result(
                "WARN",
                "Persistent memory",
                "Enabled; conversation-derived data may be stored locally",
                "Set memory.enabled to false for the privacy-first default.",
            )
        )
    else:
        results.append(_result("PASS", "Persistent memory", "Disabled"))

    required_paths = (
        ("Codex workspace", settings.codex.workspace, True),
        ("Companion skill", settings.codex.skill_path, False),
        ("Workspace policy", settings.codex.workspace / "AGENTS.md", False),
    )
    for name, path, directory in required_paths:
        exists = path.is_dir() if directory else path.is_file()
        if exists:
            results.append(_result("PASS", name, str(path)))
        else:
            results.append(
                _result(
                    "FAIL",
                    name,
                    f"Missing: {path}",
                    "Restore this file from the repository and run initialization again.",
                )
            )

    return results


def _run_command(command: list[str], *, environment: dict[str, str]) -> subprocess.CompletedProcess[str] | None:
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=12,
            env=environment,
            creationflags=creationflags,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def codex_checks(settings: Settings) -> list[CheckResult]:
    executable = resolve_codex_executable(settings.codex.executable)
    environment = codex_subprocess_environment(settings.codex.proxy_url)
    version = _run_command([executable, "--version"], environment=environment)
    if version is None or version.returncode != 0:
        return [
            _result(
                "FAIL",
                "Codex CLI",
                f"Could not run: {executable}",
                "Install or open Codex, or set codex.executable to the full codex.exe path.",
            )
        ]

    version_text = (version.stdout or version.stderr).strip().splitlines()
    results = [
        _result(
            "PASS",
            "Codex CLI",
            version_text[0] if version_text else executable,
        )
    ]
    login = _run_command([executable, "login", "status"], environment=environment)
    login_text = "" if login is None else f"{login.stdout}\n{login.stderr}".strip()
    if login is not None and login.returncode == 0 and "logged in" in login_text.lower():
        results.append(_result("PASS", "Codex login", login_text.splitlines()[0]))
    else:
        results.append(
            _result(
                "FAIL",
                "Codex login",
                login_text.splitlines()[0] if login_text else "Login status could not be confirmed",
                "Run `codex login`, finish sign-in, then run this doctor again.",
            )
        )
    return results


def _proxy_check(name: str, url: str) -> CheckResult:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return _result(
            "FAIL",
            name,
            "Invalid proxy URL",
            "Use a URL such as http://127.0.0.1:7890, or leave it empty.",
        )
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    try:
        with socket.create_connection((parsed.hostname, port), timeout=1.5):
            pass
    except OSError as error:
        return _result(
            "FAIL",
            name,
            f"Cannot connect to {parsed.hostname}:{port} ({error})",
            "Start the proxy application, correct the port, or clear this proxy setting.",
        )
    return _result("PASS", name, f"Reachable at {parsed.hostname}:{port}")


def network_checks(settings: Settings) -> list[CheckResult]:
    results: list[CheckResult] = []
    seen_proxies: set[str] = set()
    for name, url in (
        ("Codex proxy", settings.codex.proxy_url),
        ("Weather proxy", settings.weather.proxy_url if settings.weather.enabled else ""),
    ):
        if not url:
            results.append(_result("INFO", name, "Not configured"))
        elif url not in seen_proxies:
            results.append(_proxy_check(name, url))
            seen_proxies.add(url)
        else:
            results.append(_result("PASS", name, "Uses the already checked Codex proxy"))

    family = socket.AF_INET6 if ":" in settings.onebot.host else socket.AF_INET
    probe = socket.socket(family, socket.SOCK_STREAM)
    try:
        probe.bind((settings.onebot.host, settings.onebot.port))
    except OSError:
        results.append(
            _result(
                "INFO",
                "OneBot port",
                f"{settings.onebot.host}:{settings.onebot.port} is already in use; this is expected if the bridge is running",
                "If the bridge is stopped, find the conflicting process or choose another matching port in both configurations.",
            )
        )
    else:
        results.append(
            _result(
                "PASS",
                "OneBot port",
                f"{settings.onebot.host}:{settings.onebot.port} is available for the bridge",
            )
        )
    finally:
        probe.close()
    return results


def runtime_checks(settings: Settings) -> list[CheckResult]:
    results: list[CheckResult] = []
    runtime_config = settings.root / "scripts" / "runtime.local.psd1"
    if not runtime_config.is_file():
        results.append(
            _result(
                "WARN",
                "Windows autostart config",
                "scripts/runtime.local.psd1 is missing; manual startup still works",
                "Run scripts/initialize.ps1 and fill the local NapCat and QQ paths before enabling autostart.",
            )
        )
        return results

    text = runtime_config.read_text(encoding="utf-8", errors="replace")
    if r"C:\Path\To" in text:
        results.append(
            _result(
                "WARN",
                "Windows autostart config",
                "The example NapCat or QQ path has not been replaced",
                "Edit scripts/runtime.local.psd1 before installing the scheduled task.",
            )
        )
        return results

    values: dict[str, str] = {}
    for key in ("NapCatRoot", "QQPath", "OneBotWsPort"):
        match = re.search(
            rf"(?m)^\s*{key}\s*=\s*(?:'([^']*)'|\"([^\"]*)\"|(\d+))\s*$",
            text,
        )
        if match:
            values[key] = next(value for value in match.groups() if value is not None)

    napcat_root = Path(values.get("NapCatRoot", ""))
    qq_path = Path(values.get("QQPath", ""))
    missing = [
        path
        for path in (
            napcat_root / "NapCatWinBootMain.exe",
            napcat_root / "NapCatWinBootHook.dll",
            qq_path,
        )
        if not path.is_file()
    ]
    if missing:
        results.append(
            _result(
                "WARN",
                "Windows autostart paths",
                f"{len(missing)} required startup path(s) were not found",
                "Correct NapCatRoot and QQPath in scripts/runtime.local.psd1; manual startup still works.",
            )
        )
    else:
        results.append(_result("PASS", "Windows autostart paths", "NapCat and QQ files exist"))

    configured_port = values.get("OneBotWsPort")
    if configured_port and int(configured_port) == settings.onebot.port:
        results.append(_result("PASS", "Autostart OneBot port", configured_port))
    else:
        results.append(
            _result(
                "WARN",
                "Autostart OneBot port",
                "Does not match config.local.toml",
                f"Set OneBotWsPort to {settings.onebot.port} in scripts/runtime.local.psd1.",
            )
        )
    return results


def run_doctor(config_path: Path) -> tuple[list[CheckResult], bool]:
    results: list[CheckResult] = []
    if not config_path.is_file():
        return (
            [
                _result(
                    "FAIL",
                    "Local configuration",
                    f"Missing: {config_path}",
                    "Run `powershell -ExecutionPolicy Bypass -File .\\scripts\\initialize.ps1` first.",
                )
            ],
            False,
        )

    try:
        settings = load_settings(config_path)
    except Exception as error:
        return (
            [
                _result(
                    "FAIL",
                    "Local configuration",
                    str(error),
                    "Compare config.local.toml with config.example.toml and correct the named field.",
                )
            ],
            False,
        )

    results.append(_result("PASS", "Local configuration", str(config_path)))
    results.extend(configuration_checks(settings))
    results.extend(codex_checks(settings))
    results.extend(network_checks(settings))
    results.extend(runtime_checks(settings))
    return results, True


def _print_report(results: list[CheckResult], config_loaded: bool) -> None:
    print("QQ Codex Companion doctor")
    print("=" * 29)
    for check in results:
        print(f"[{check.level:4}] {check.name}: {check.detail}")
        if check.fix:
            print(f"       Fix: {check.fix}")

    failures = sum(check.level == "FAIL" for check in results)
    warnings = sum(check.level == "WARN" for check in results)
    print("-" * 29)
    if failures:
        print(f"Result: not ready ({failures} failure(s), {warnings} warning(s)).")
    elif config_loaded:
        print(f"Result: ready for a local start ({warnings} warning(s)).")
        print("Next: uv run qq-codex-companion")
        print("Optional live model check: uv run qq-codex-selftest")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Check local configuration, Codex login, proxy, and OneBot readiness."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(os.environ.get("QQ_CODEX_CONFIG", "config.local.toml")),
        help="Path to config.local.toml (defaults to QQ_CODEX_CONFIG or ./config.local.toml)",
    )
    args = parser.parse_args()
    results, loaded = run_doctor(args.config.resolve())
    _print_report(results, loaded)
    if any(check.level == "FAIL" for check in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
