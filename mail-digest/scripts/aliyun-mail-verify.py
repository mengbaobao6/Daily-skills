#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阿里企业邮箱 接入验证脚本（IMAP 收信 + SMTP 发信鉴权自检）

用途：
  在正式配置自动化之前，先验证「账号 + 三方客户端安全密码」能不能登录成功，
  并顺带看一眼收件箱最近几封邮件，确认权限真的放开了。

凭据读取顺序（优先命令行参数）：
  1) 命令行参数：  python scripts/aliyun-mail-verify.py --user you@yourdomain.com --pass 'xxxx'
  2) 本地凭据文件：aliyun-mail-cred.json —— 依次在「当前目录 → 脚本上一级目录 → 脚本目录」查找
         { "user": "you@yourdomain.com", "password": "邮箱密码或三方客户端安全密码" }
  3) 环境变量：    ALIYUN_MAIL_USER / ALIYUN_MAIL_PASSWORD
  ⚠️ 凭据文件不得提交 git。

推荐用法（避免密码出现在聊天/历史记录里）：
  先把凭据写进 aliyun-mail-cred.json，再运行：
      python scripts/aliyun-mail-verify.py

站点说明：默认使用中国内地站点 qiye.aliyun.com。
  若你的邮箱在新加坡/中国香港/德国/美国站点，用 --site 指定：
      python scripts/aliyun-mail-verify.py --site sg      # 新加坡
      python scripts/aliyun-mail-verify.py --site hk      # 中国香港
      python scripts/aliyun-mail-verify.py --site us      # 美国
      python scripts/aliyun-mail-verify.py --site de      # 德国
"""

import argparse
import imaplib
import json
import os
import smtplib
import ssl
import sys
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

SITES = {
    "cn": {"imap": "imap.qiye.aliyun.com", "smtp": "smtp.qiye.aliyun.com", "pop": "pop.qiye.aliyun.com"},
    "sg": {"imap": "imap.sg.aliyun.com", "smtp": "smtp.sg.aliyun.com", "pop": "pop.sg.aliyun.com"},
    "hk": {"imap": "imap.hk.aliyun.com", "smtp": "smtp.hk.aliyun.com", "pop": "pop.hk.aliyun.com"},
    "us": {"imap": "imap.us.alibabacloud.com", "smtp": "smtp.us.alibabacloud.com", "pop": "pop.us.alibabacloud.com"},
    "de": {"imap": "imap.de.alibabacloud.com", "smtp": "smtp.de.alibabacloud.com", "pop": "pop.de.alibabacloud.com"},
}

CRED_NAME = "aliyun-mail-cred.json"


def cred_candidates():
    """按优先级返回凭据文件的候选路径：当前目录 → 脚本上一级 → 脚本目录。"""
    here = os.path.dirname(os.path.abspath(__file__))
    return [
        os.path.join(os.getcwd(), CRED_NAME),
        os.path.join(os.path.dirname(here), CRED_NAME),
        os.path.join(here, CRED_NAME),
    ]


def load_credentials(args):
    if args.user and args.password:
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
            print(f"[凭据] 文件存在但缺少 user / password 字段：{path}")
        except Exception as exc:
            print(f"[凭据] 读取 {path} 失败：{exc}")
    user = os.environ.get("ALIYUN_MAIL_USER")
    pwd = os.environ.get("ALIYUN_MAIL_PASSWORD")
    if user and pwd:
        print("[凭据] 已从环境变量读取")
        return user, pwd
    return None, None


def explain_imap_error(err_text):
    t = err_text.upper()
    tips = []
    if "AUTHENTICATIONFAILED" in t or "LOGIN FAILED" in t or "AUTHENTICATE" in t or "606" in t:
        tips.append("账号或密码被拒绝。分两种情况判断：")
        tips.append("  a) 管理员【已启用】三方客户端安全密码 → 必须填安全密码，原邮箱登录密码会失效。")
        tips.append("     生成路径：网页版右上角「设置」→「查看更多设置」→ 账户与安全 → 账户安全")
        tips.append("               → 三方客户端登录安全管理 → 生成新密码（16位，仅显示一次，务必备份）")
        tips.append("  b) 管理员【未启用】该功能 → 不用找安全密码，直接填邮箱登录密码即可。")
    if "PRIVACY" in t or "SECURITY" in t or "DENIED" in t or "FORBIDDEN" in t or "NOT ALLOWED" in t:
        tips.append("服务端明确拒绝了本次登录，很可能是管理员限制了「三方客户端登录」。")
        tips.append("请让邮箱管理员在 管理后台 → 安全管理 → 账号安全策略 → 三方客户端登录安全")
        tips.append("中把「启用范围」设为包含你的账号。")
    if "IMAP" in t and ("DISABLE" in t or "CLOSE" in t or "NOT OPEN" in t):
        tips.append("该账号的 IMAP 服务权限未开启。请管理员在")
        tips.append("管理后台 → 组织与用户 → 员工账号 → 功能权限 → 客户端配置 中开启 IMAP 与 SMTP。")
    return tips


def decode_maybe(value):
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def test_imap(host, user, pwd, show_messages=5):
    print("\n" + "=" * 66)
    print(f"[1/2] IMAP 收信测试  {host}:993 (SSL)")
    print("=" * 66)
    ctx = ssl.create_default_context()
    try:
        conn = imaplib.IMAP4_SSL(host, 993, ssl_context=ctx)
    except Exception as exc:
        print(f"  [FAIL] 建立连接失败：{type(exc).__name__}: {exc}")
        print("  → 请检查本机网络、防火墙或代理是否放行 993 端口。")
        return False

    try:
        print(f"  服务端问候：{conn.welcome.decode('utf-8', 'ignore') if conn.welcome else '(无)'}")
    except Exception:
        pass

    try:
        conn.login(user, pwd)
        print("  [OK] 登录成功 —— 三方客户端权限与安全密码均正常 ✅")
    except imaplib.IMAP4.error as exc:
        print(f"  [FAIL] 登录被拒绝：{exc}")
        for tip in explain_imap_error(str(exc)):
            print(f"         · {tip}")
        try:
            conn.logout()
        except Exception:
            pass
        return False
    except Exception as exc:
        print(f"  [FAIL] 未知错误：{type(exc).__name__}: {exc}")
        return False

    try:
        typ, data = conn.select("INBOX", readonly=True)
        if typ != "OK":
            print(f"  [WARN] 打开收件箱返回 {typ}：{data}")
            conn.logout()
            return True
        total = int(data[0]) if data and data[0] else 0
        print(f"  [OK] 收件箱可读，共 {total} 封邮件")

        if total and show_messages:
            typ, data = conn.search(None, "ALL")
            ids = data[0].split()
            recent = ids[-show_messages:][::-1]
            print(f"\n  最近 {len(recent)} 封邮件：")
            print("  " + "-" * 62)
            for i, mid in enumerate(recent, 1):
                typ, msg_data = conn.fetch(mid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                raw = b""
                for part in msg_data:
                    if isinstance(part, tuple) and len(part) > 1:
                        raw += part[1]
                text = raw.decode("utf-8", "ignore")
                meta = {"from": "", "subject": "", "date": ""}
                for line in text.splitlines():
                    low = line.lower()
                    if low.startswith("from:"):
                        meta["from"] = decode_maybe(line[5:].strip())
                    elif low.startswith("subject:"):
                        meta["subject"] = decode_maybe(line[8:].strip())
                    elif low.startswith("date:"):
                        meta["date"] = line[5:].strip()
                date_txt = meta["date"]
                try:
                    date_txt = parsedate_to_datetime(meta["date"]).astimezone().strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass
                print(f"  {i}. [{date_txt}]")
                print(f"     发件人：{meta['from'][:70]}")
                print(f"     主题：{meta['subject'][:70]}")
            print("  " + "-" * 62)
    except Exception as exc:
        print(f"  [WARN] 已登录但读取收件箱出错：{type(exc).__name__}: {exc}")
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return True


def test_smtp(host, user, pwd):
    print("\n" + "=" * 66)
    print(f"[2/2] SMTP 发信鉴权测试  {host}:465 (SSL)")
    print("=" * 66)
    ctx = ssl.create_default_context()
    try:
        server = smtplib.SMTP_SSL(host, 465, context=ctx, timeout=15)
    except Exception as exc:
        print(f"  [FAIL] 建立连接失败：{type(exc).__name__}: {exc}")
        print("  → 请检查 465 端口是否被网络策略拦截。")
        return False
    try:
        print(f"  服务端问候：{server.ehlo()[1].decode('utf-8', 'ignore') if isinstance(server.ehlo()[1], bytes) else server.ehlo()[1]}")
    except Exception:
        pass
    try:
        server.login(user, pwd)
        print("  [OK] SMTP 鉴权成功 —— 具备发信能力 ✅")
        print("  （本次未发送任何邮件，仅做登录鉴权）")
        return True
    except smtplib.SMTPAuthenticationError as exc:
        print(f"  [FAIL] 鉴权被拒绝：{exc}")
        for tip in explain_imap_error(str(exc)):
            print(f"         · {tip}")
        return False
    except Exception as exc:
        print(f"  [FAIL] 未知错误：{type(exc).__name__}: {exc}")
        return False
    finally:
        try:
            server.quit()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="阿里企业邮箱 IMAP/SMTP 接入自检")
    ap.add_argument("--user", help="完整邮箱地址，例如 you@yourdomain.com")
    ap.add_argument("--pass", dest="password", help="三方客户端安全密码")
    ap.add_argument("--site", default="cn", choices=sorted(SITES.keys()), help="邮箱站点，默认 cn")
    ap.add_argument("--count", type=int, default=5, help="展示最近几封邮件，默认 5")
    args = ap.parse_args()

    hosts = SITES[args.site]
    print("阿里企业邮箱接入自检")
    print(f"站点：{args.site}  |  IMAP {hosts['imap']}  |  SMTP {hosts['smtp']}")

    user, pwd = load_credentials(args)
    if not user or not pwd:
        print("\n未找到凭据。请任选一种方式提供：")
        print("  1) 命令行：--user you@yourdomain.com --pass '密码'")
        print("  2) 凭据文件 aliyun-mail-cred.json（推荐，密码不进命令行历史/聊天记录）")
        print("     依次在 当前目录 / 脚本上一级目录 / 脚本目录 查找，内容格式：")
        print('     {"user": "you@yourdomain.com", "password": "邮箱密码或三方客户端安全密码"}')
        print("  3) 环境变量：ALIYUN_MAIL_USER / ALIYUN_MAIL_PASSWORD")
        return 2
    print(f"账号：{user}  密码：{'*' * 8}（{len(pwd)} 位）")

    ok_imap = test_imap(hosts["imap"], user, pwd, args.count)
    ok_smtp = test_smtp(hosts["smtp"], user, pwd)

    print("\n" + "=" * 66)
    print("结论")
    print("=" * 66)
    print(f"  IMAP 收信：{'可用 ✅' if ok_imap else '不可用 ❌'}")
    print(f"  SMTP 发信：{'可用 ✅' if ok_smtp else '不可用 ❌'}")
    if ok_imap and ok_smtp:
        print("\n  两项都通过，可以进入下一步：配置邮件技能或接入自动化汇总。")
        return 0
    print("\n  存在失败项，请按上方提示处理（多与「三方客户端安全密码」或管理员放行有关）。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
