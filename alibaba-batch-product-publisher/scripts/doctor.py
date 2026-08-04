#!/usr/bin/env python3
"""Secret-safe runtime and read-only API health check."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from api_client import AlibabaClient, load_environment, response_payload


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:10]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Check the portable publisher runtime safely.")
    root.add_argument("--config", "--mcp-config", dest="config", type=Path)
    root.add_argument("--offline", action="store_true", help="Do not call Alibaba APIs.")
    return root


def main() -> None:
    args = parser().parse_args()
    result: dict = {
        "ok": False,
        "python": sys.version.split()[0],
        "config_loaded": False,
        "token_loaded": False,
        "read_only_product_list": "skipped" if args.offline else "pending",
    }
    try:
        environment = load_environment(args.config)
        result["config_loaded"] = True
        result["app_key_fingerprint"] = fingerprint(environment["ALIBABA_APP_KEY"])
        token_path = Path(environment["ALIBABA_TOKEN_FILE"])
        token = json.loads(token_path.read_text(encoding="utf-8-sig"))
        if not token.get("access_token"):
            raise ValueError("Token file has no access_token.")
        result["token_loaded"] = True
        result["token_file"] = str(token_path)
        if not args.offline:
            response = AlibabaClient(environment).request(
                "alibaba.icbu.product.list",
                {"page_size": 1, "current_page": 1, "language": "ENGLISH"},
            )
            payload = response_payload(response)
            success = bool(payload) and "error_response" not in response
            result["read_only_product_list"] = "passed" if success else "failed"
            result["request_id"] = payload.get("request_id") or response.get("request_id") or ""
            if not success:
                result["api_error_code"] = payload.get("error_code") or payload.get("code") or "unknown"
                raise RuntimeError("Read-only product.list did not report success.")
        result["ok"] = True
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
