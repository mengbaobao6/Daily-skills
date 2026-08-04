#!/usr/bin/env python3
"""Multipart Alibaba Photobank upload with local content identity evidence."""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
from pathlib import Path
import time
import urllib.request
import uuid

from api_client import AlibabaClient


SUPPORTED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def local_payload(path: Path) -> tuple[dict, bytes]:
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Local image does not exist: {path}")
    if path.suffix.lower() not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported image type {path.suffix}: {path}")
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Local image is empty: {path}")
    return {
        "local_path": str(path),
        "file_name": path.name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }, data


def local_identity(path: Path) -> dict:
    identity, _ = local_payload(path)
    return identity


def upload_payload(client: AlibabaClient, identity: dict, data: bytes, timeout: int = 60) -> dict:
    path = Path(str(identity["local_path"]))
    if hashlib.sha256(data).hexdigest() != str(identity["sha256"]):
        raise ValueError(f"Image bytes changed after hashing: {path}")
    params = {
        "app_key": client.environment["ALIBABA_APP_KEY"],
        "access_token": client.access_token,
        "file_name": path.name,
        "method": "alibaba.icbu.photobank.upload",
        "sign_method": "sha256",
        "simplify": "true",
        "timestamp": str(int(time.time() * 1000)),
    }
    source = "".join(f"{key}{params[key]}" for key in sorted(params))
    params["sign"] = hmac.new(
        client.environment["ALIBABA_APP_SECRET"].encode(),
        source.encode(),
        hashlib.sha256,
    ).hexdigest().upper()
    boundary = "----Codex" + uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in params.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image_bytes"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
    )
    parts.extend((data, f"\r\n--{boundary}--\r\n".encode()))
    request = urllib.request.Request(
        client.environment.get("ALIBABA_GATEWAY", "https://open-api.alibaba.com/sync").rstrip("/") + "/",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = json.loads(response.read().decode("utf-8"))
    payload = raw.get("alibaba_icbu_photobank_upload_response", {})
    uploaded = payload.get("upload_image_response", {})
    url = str(uploaded.get("photobank_url") or "")
    if url.startswith("//"):
        url = "https:" + url
    return {
        **identity,
        "file_id": str(uploaded.get("file_id") or ""),
        "url": url,
        "request_id": payload.get("request_id"),
        "success": bool(uploaded.get("file_id") and url),
        "raw_response": raw,
    }


def upload_one(client: AlibabaClient, path: Path, timeout: int = 60) -> dict:
    identity, data = local_payload(path)
    return upload_payload(client, identity, data, timeout)
