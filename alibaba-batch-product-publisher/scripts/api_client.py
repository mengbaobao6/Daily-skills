#!/usr/bin/env python3
"""Minimal, agent-agnostic and secret-safe Alibaba ICBU sync client."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import time
import urllib.parse
import urllib.request


REQUIRED_SETTINGS = ("ALIBABA_APP_KEY", "ALIBABA_APP_SECRET", "ALIBABA_TOKEN_FILE")


def default_config() -> Path:
    """Find a config without depending on a particular agent product."""
    candidates: list[Path] = []
    configured = os.environ.get("ALIBABA_CONFIG_FILE", "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        (
            Path.cwd() / ".alibaba-publisher.json",
            Path.home() / ".config" / "alibaba-publisher" / "config.json",
            Path.home() / ".workbuddy" / "mcp.json",
            Path.home() / ".codex" / "mcp.json",
        )
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise FileNotFoundError(
        "No Alibaba configuration found. Set ALIBABA_APP_KEY, "
        "ALIBABA_APP_SECRET and ALIBABA_TOKEN_FILE, or pass --config."
    )


def _config_environment(config: dict) -> dict:
    """Accept flat, generic env-wrapper, and legacy MCP config shapes."""
    if any(key in config for key in REQUIRED_SETTINGS):
        return config
    if isinstance(config.get("env"), dict):
        return config["env"]
    servers = config.get("mcpServers")
    if isinstance(servers, dict):
        named = servers.get("alibaba-api-tools")
        if isinstance(named, dict) and isinstance(named.get("env"), dict):
            return named["env"]
        for server in servers.values():
            if isinstance(server, dict) and isinstance(server.get("env"), dict):
                env = server["env"]
                if any(key in env for key in REQUIRED_SETTINGS):
                    return env
    raise ValueError("Unsupported Alibaba config shape; see assets/config.example.json.")


def load_environment(config_path: Path | None = None) -> dict[str, str]:
    process_values = {key: os.environ.get(key, "").strip() for key in REQUIRED_SETTINGS}
    optional_values = {
        key: os.environ[key].strip()
        for key in ("ALIBABA_GATEWAY",)
        if os.environ.get(key, "").strip()
    }
    source_path: Path | None = None
    file_values: dict = {}
    if config_path is not None:
        source_path = config_path.expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Alibaba config does not exist: {source_path}")
    elif not all(process_values.values()):
        source_path = default_config()
    if source_path is not None:
        config = json.loads(source_path.read_text(encoding="utf-8-sig"))
        file_values = _config_environment(config)

    env = {str(key): str(value) for key, value in file_values.items() if value is not None}
    env.update({key: value for key, value in process_values.items() if value})
    env.update(optional_values)
    missing = [key for key in REQUIRED_SETTINGS if not env.get(key)]
    if missing:
        raise ValueError("Missing Alibaba settings: " + ", ".join(missing))

    token_path = Path(env["ALIBABA_TOKEN_FILE"]).expanduser()
    if not token_path.is_absolute() and source_path is not None:
        token_path = source_path.parent / token_path
    env["ALIBABA_TOKEN_FILE"] = str(token_path.resolve())
    return env


def response_payload(response: dict) -> dict:
    key = next((key for key in response if key.startswith("alibaba_")), None)
    return response.get(key, {}) if key else {}


class AlibabaClient:
    def __init__(self, environment: dict[str, str]):
        self.environment = environment
        token = json.loads(Path(environment["ALIBABA_TOKEN_FILE"]).read_text(encoding="utf-8-sig"))
        self.access_token = str(token.get("access_token") or "")
        if not self.access_token:
            raise ValueError("Configured token file has no access_token.")

    def request(self, method: str, business: dict | None = None, timeout: int = 60) -> dict:
        params: dict[str, str] = {
            "app_key": self.environment["ALIBABA_APP_KEY"],
            "access_token": self.access_token,
            "method": method,
            "timestamp": str(int(time.time() * 1000)),
            "sign_method": "sha256",
            "simplify": "true",
        }
        for key, value in (business or {}).items():
            if value is None or value == "":
                continue
            params[key] = (
                json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                if isinstance(value, (dict, list))
                else str(value)
            )
        source = "".join(f"{key}{params[key]}" for key in sorted(params))
        params["sign"] = hmac.new(
            self.environment["ALIBABA_APP_SECRET"].encode(),
            source.encode(),
            hashlib.sha256,
        ).hexdigest().upper()
        request = urllib.request.Request(
            self.environment.get("ALIBABA_GATEWAY", "https://open-api.alibaba.com/sync").rstrip("/") + "/",
            data=urllib.parse.urlencode(params).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
