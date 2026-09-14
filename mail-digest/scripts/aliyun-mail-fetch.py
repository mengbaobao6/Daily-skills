#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里企业邮箱邮件拉取工具（供每日汇总自动化调用）

功能：通过 IMAP 读取指定时间窗口内的收件箱邮件，输出结构化摘要，
      供上层（AI 助手 / 自动化任务）做分类与汇总。

用法示例：
    # 最近 24 小时，最多 15 封，附 200 字正文预览
    python scripts/aliyun-mail-fetch.py --hours 24 --limit 15 --preview 200

    # 最近 72 小时，JSON 格式输出
    python scripts/aliyun-mail-fetch.py --hours 72 --limit 30 --format json

    # 只取未读
    python scripts/aliyun-mail-fetch.py --hours 24 --unread-only

    # 查看某一封的完整正文（用列表里的 message-id）
    python scripts/aliyun-mail-fetch.py --show <message-id>

凭据来源（按优先级）：
    1) 命令行 --user / --pass
    2) aliyun-mail-cred.json —— 依次在「当前目录 → 脚本上一级目录 → 脚本目录」查找
    3) 环境变量 ALIYUN_MAIL_USER / ALIYUN_MAIL_PASSWORD
    ⚠️ 凭据文件不得提交 git。

退出码：0 成功；1 登录或读取失败；2 未找到凭据；3 未找到指定邮件
"""

import argparse
import email
import imaplib
import json
import os
import re
import ssl
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

SITES = {
    "cn": "imap.qiye.aliyun.com",
    "sg": "imap.sg.aliyun.com",
    "hk": "imap.hk.aliyun.com",
    "us": "imap.us.alibabacloud.com",
    "de": "imap.de.alibabacloud.com",
}

CRED_NAME = "aliyun-mail-cred.json"
CN_TZ = timezone(timedelta(hours=8))
PREVIEW_MAX_BYTES = 300 * 1024  # 超过此大小的邮件跳过正文预览，避免拉取附件
IMAP_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def cred_candidates():
    """按优先级返回凭据文件的候选路径：当前目录 → 脚本上一级 → 脚本目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(os.getcwd(), CRED_NAME),
        os.path.join(os.path.dirname(here), CRED_NAME),
        os.path.join(here, CRED_NAME),
    ]


def load_credentials(args):
    if getattr(args, "user", None) and getattr(args, "password", None):
        return args.user, args.password
    for path in cred_candidates():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            user = data.get("user") or data.get("username") or data.get("email")
            pwd = data.get("password") or data.get("pass")
            if user and pwd:
                print(f"[凭据] 已从 {path} 读取（{user}）")
                return user, pwd
        except Exception:
            continue
    user = os.environ.get("ALIYUN_MAIL_USER")
    pwd = os.environ.get("ALIYUN_MAIL_PASSWORD")
    if user and pwd:
        print("[凭据] 已从环境变量读取")
        return user, pwd
    return None, None


def dh(value):
    """解码 MIME 编码的头部字段。"""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value))).strip()
    except Exception:
        return value.strip()


def imap_date(dt):
    return f"{dt.day:02d}-{IMAP_MONTHS[dt.month - 1]}-{dt.year}"


def strip_html(text):
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"&(amp|lt|gt|quot|#39);", " ", text)
    return re.sub(r"[ \t\r\f\v]+", " ", text)


def extract_body_text(msg, limit):
    """从邮件对象中提取纯文本正文，截断到 limit 字符。"""
    chunks = []
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if "attachment" in disp.lower():
                continue
            if ctype not in ("text/plain", "text/html"):
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            try:
                body = payload.decode(charset, "ignore")
            except Exception:
                body = payload.decode("utf-8", "ignore")
            chunks.append(strip_html(body) if ctype == "text/html" else body)
            if ctype == "text/plain":
                break
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            body = payload.decode(charset, "ignore") if payload else ""
        except Exception:
            body = ""
        chunks.append(strip_html(body) if msg.get_content_type() == "text/html" else body)

    text = "\n".join(c for c in chunks if c)
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return text[:limit]


def connect(host, user, pwd):
    ctx = ssl.create_default_context()
    conn = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx)
    conn.login(user, pwd)
    return conn


def fetch_headers(conn, mid):
    """一次性取回 flags / 大小 / 结构 / 关键头部。"""
    typ, data = conn.fetch(
        mid,
        "(FLAGS RFC822.SIZE BODYSTRUCTURE BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID TO)])",
    )
    if typ != "OK" or not data:
        return None
    prefix = ""
    raw = b""
    for part in data:
        if isinstance(part, tuple) and len(part) > 1:
            if not prefix and part[0]:
                prefix = part[0].decode("utf-8", "ignore")
            raw += part[1]
        elif isinstance(part, bytes) and not prefix:
            prefix = part.decode("utf-8", "ignore")

    flags_match = re.search(r"FLAGS \(([^)]*)\)", prefix)
    size_match = re.search(r"RFC822\.SIZE (\d+)", prefix)
    flags = flags_match.group(1) if flags_match else ""
    size = int(size_match.group(1)) if size_match else 0

    header_msg = email.message_from_bytes(raw)
    date_raw = header_msg.get("Date", "")
    try:
        dt = parsedate_to_datetime(date_raw).astimezone(CN_TZ)
    except Exception:
        dt = None

    return {
        "subject": dh(header_msg.get("Subject", "")) or "(无主题)",
        "from": dh(header_msg.get("From", "")),
        "to": dh(header_msg.get("To", "")),
        "message_id": (header_msg.get("Message-ID", "") or "").strip(),
        "date": dt,
        "is_read": "\\Seen" in flags,
        "flagged": "\\Flagged" in flags,
        "size": size,
        "has_attachment": "ATTACHMENT" in prefix.upper(),
    }


def collect(conn, hours, limit, unread_only):
    cutoff = datetime.now(CN_TZ) - timedelta(hours=hours)
    typ, data = conn.search(None, "SINCE", imap_date(cutoff))
    if typ != "OK":
        raise RuntimeError(f"IMAP 搜索失败：{typ}")
    ids = data[0].split()
    if not ids:
        return [], 0
    candidates = ids[-max(limit * 4, 40):]
    items = []
    for mid in reversed(candidates):
        info = fetch_headers(conn, mid)
        if not info:
            continue
        if info["date"] is None or info["date"] < cutoff:
            continue
        if unread_only and info["is_read"]:
            continue
        info["uid"] = mid.decode() if isinstance(mid, bytes) else str(mid)
        items.append(info)
        if len(items) >= limit * 2:
            break

    seen = set()
    deduped = []
    for it in items:
        key = (
            it["subject"],
            it["from"],
            it["date"].strftime("%Y-%m-%d %H:%M") if it["date"] else "",
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    removed = len(items) - len(deduped)
    return deduped[:limit], removed


def attach_previews(conn, items):
    for info in items:
        if info["size"] and info["size"] > PREVIEW_MAX_BYTES:
            info["preview"] = "(邮件过大，已跳过正文预览)"
            continue
        try:
            typ, data = conn.fetch(info["uid"].encode(), "(BODY.PEEK[])")
            raw = b""
            for part in data:
                if isinstance(part, tuple) and len(part) > 1:
                    raw += part[1]
            if not raw:
                info["preview"] = ""
                continue
            msg = email.message_from_bytes(raw)
            info["preview"] = extract_body_text(msg, info["preview_limit"])
        except Exception as exc:
            info["preview"] = f"(正文读取失败：{type(exc).__name__})"


def show_message(conn, message_id):
    typ, data = conn.search(None, "HEADER", "Message-ID", f'"{message_id}"')
    if typ != "OK" or not data or not data[0].split():
        return None
    mid = data[0].split()[-1]
    typ, data = conn.fetch(mid, "(BODY.PEEK[])")
    raw = b""
    for part in data:
        if isinstance(part, tuple) and len(part) > 1:
            raw += part[1]
    if not raw:
        return None
    msg = email.message_from_bytes(raw)
    return {
        "subject": dh(msg.get("Subject", "")) or "(无主题)",
        "from": dh(msg.get("From", "")),
        "to": dh(msg.get("To", "")),
        "date": dh(msg.get("Date", "")),
        "body": extract_body_text(msg, 8000),
    }


def render_text(user, hours, items, removed=0):
    lines = [
        f"阿里邮箱拉取结果",
        f"账号：{user}",
        f"窗口：最近 {hours} 小时",
        f"数量：{len(items)} 封" + (f"（已合并 {removed} 封重复邮件）" if removed else ""),
        "=" * 60,
    ]
    if not items:
        lines.append("（该时间窗口内没有新邮件）")
        return "\n".join(lines)
    for i, m in enumerate(items, 1):
        when = m["date"].strftime("%Y-%m-%d %H:%M") if m["date"] else "?"
        badges = []
        badges.append("未读" if not m["is_read"] else "已读")
        if m["flagged"]:
            badges.append("星标")
        badges.append("有附件" if m["has_attachment"] else "无附件")
        lines.append(f"[{i}] {when} | {' | '.join(badges)}")
        lines.append(f"    发件人：{m['from']}")
        lines.append(f"    主题：{m['subject']}")
        if m.get("preview"):
            preview = m["preview"].replace("\n", " ").strip()
            lines.append(f"    正文预览：{preview}")
        lines.append(f"    message-id：{m['message_id']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main():
    ap = argparse.ArgumentParser(description="阿里企业邮箱邮件拉取工具")
    ap.add_argument("--user")
    ap.add_argument("--pass", dest="password")
    ap.add_argument("--site", default="cn", choices=sorted(SITES.keys()))
    ap.add_argument("--hours", type=int, default=24, help="时间窗口小时数，默认 24")
    ap.add_argument("--limit", type=int, default=15, help="最多返回封数，默认 15")
    ap.add_argument("--preview", type=int, default=0, help="正文预览字符数，0 表示不取正文字符")
    ap.add_argument("--unread-only", action="store_true", help="只返回未读邮件")
    ap.add_argument("--format", default="text", choices=["text", "json"])
    ap.add_argument("--mailbox", default="INBOX")
    ap.add_argument("--show", help="按 message-id 输出单封邮件完整正文")
    args = ap.parse_args()

    user, pwd = load_credentials(args)
    if not user or not pwd:
        print("[错误] 未找到阿里邮箱凭据。请任选一种方式提供：")
        print("       1) 命令行：--user you@yourdomain.com --pass '密码'")
        print('       2) 建立 aliyun-mail-cred.json：{"user": "...", "password": "..."}')
        print("          （依次在 当前目录 / 脚本上一级目录 / 脚本目录 查找）")
        print("       3) 环境变量：ALIYUN_MAIL_USER / ALIYUN_MAIL_PASSWORD")
        return 2

    host = SITES[args.site]
    try:
        conn = connect(host, user, pwd)
    except imaplib.IMAP4.error as exc:
        print(f"[错误] IMAP 登录失败：{exc}")
        print("       常见原因：密码错误（若管理员启用了三方客户端安全密码，需使用安全密码）、")
        print("       或管理员未放行该账号的三方客户端登录。")
        return 1
    except Exception as exc:
        print(f"[错误] 无法连接 {host}:993 —— {type(exc).__name__}: {exc}")
        return 1

    try:
        typ, data = conn.select(args.mailbox, readonly=True)
        if typ != "OK":
            print(f"[错误] 无法打开邮箱文件夹 {args.mailbox}：{data}")
            return 1

        if args.show:
            result = show_message(conn, args.show)
            if not result:
                print(f"[错误] 未找到 message-id 为 {args.show} 的邮件。")
                return 3
            print(f"主题：{result['subject']}")
            print(f"发件人：{result['from']}")
            print(f"收件人：{result['to']}")
            print(f"时间：{result['date']}")
            print("-" * 60)
            print(result["body"])
            return 0

        items, removed = collect(conn, args.hours, args.limit, args.unread_only)
        if args.preview and items:
            for it in items:
                it["preview_limit"] = args.preview
            attach_previews(conn, items)

        if args.format == "json":
            out = []
            for m in items:
                row = {k: v for k, v in m.items() if k != "preview_limit"}
                row["date"] = m["date"].isoformat() if m["date"] else None
                out.append(row)
            print(json.dumps({"account": user, "hours": args.hours, "count": len(out),
                              "deduped": removed, "messages": out},
                             ensure_ascii=False, indent=2))
        else:
            print(render_text(user, args.hours, items, removed))
        return 0
    except Exception as exc:
        print(f"[错误] 读取邮件时出错：{type(exc).__name__}: {exc}")
        return 1
    finally:
        try:
            conn.logout()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
