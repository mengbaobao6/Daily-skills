#!/usr/bin/env python3
"""Offline portability tests for Alibaba configuration loading."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from api_client import load_environment


REQUIRED = {
    "ALIBABA_APP_KEY": "test-key",
    "ALIBABA_APP_SECRET": "test-secret",
    "ALIBABA_TOKEN_FILE": "token.json",
}


class PortableConfigTests(unittest.TestCase):
    def write_config(self, folder: Path, value: dict) -> Path:
        path = folder / "config.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        (folder / "token.json").write_text('{"access_token":"test-token"}', encoding="utf-8")
        return path

    def clean_environment(self) -> dict[str, str]:
        return {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("ALIBABA_")
        }

    def test_flat_config_and_relative_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = self.write_config(folder, REQUIRED)
            with patch.dict(os.environ, self.clean_environment(), clear=True):
                loaded = load_environment(path)
            self.assertEqual(loaded["ALIBABA_APP_KEY"], "test-key")
            self.assertEqual(Path(loaded["ALIBABA_TOKEN_FILE"]), (folder / "token.json").resolve())

    def test_generic_env_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = self.write_config(folder, {"env": REQUIRED})
            with patch.dict(os.environ, self.clean_environment(), clear=True):
                loaded = load_environment(path)
            self.assertEqual(loaded["ALIBABA_APP_SECRET"], "test-secret")

    def test_legacy_mcp_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            path = self.write_config(
                folder,
                {"mcpServers": {"alibaba-api-tools": {"env": REQUIRED}}},
            )
            with patch.dict(os.environ, self.clean_environment(), clear=True):
                loaded = load_environment(path)
            self.assertEqual(loaded["ALIBABA_APP_KEY"], "test-key")

    def test_process_environment_needs_no_agent_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            token = folder / "token.json"
            token.write_text('{"access_token":"test-token"}', encoding="utf-8")
            values = dict(REQUIRED)
            values["ALIBABA_TOKEN_FILE"] = str(token)
            with patch.dict(os.environ, values, clear=True):
                loaded = load_environment()
            self.assertEqual(loaded["ALIBABA_APP_SECRET"], "test-secret")


if __name__ == "__main__":
    unittest.main(verbosity=2)
