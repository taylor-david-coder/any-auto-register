"""
Gmail OTP Bridge (FastAPI, single file)

用途：
- 提供 /otp?to=xxx+alias@gmail.com&kw=OpenAI 接口
- 从 Gmail API 拉取最近邮件并提取 6 位验证码
- 返回纯文本验证码，供主项目 mailapi_url 模式轮询

首次使用：
1) 准备 credentials.json（Google OAuth Desktop App）
2) 执行: python gmail_bridge.py --init-auth
3) 启动: python gmail_bridge.py --host 127.0.0.1 --port 9090
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import threading
import time
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow, InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
TOKEN_FILE = "token.json"
CREDENTIALS_FILE = "credentials.json"

app = FastAPI(title="Gmail OTP Bridge", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# OAuth state 暂存（浏览器授权回调使用）
_OAUTH_STATE_LOCK = threading.Lock()
_OAUTH_STATES: Dict[str, Dict[str, Any]] = {}

# 按 alias 记录最近已返回验证码邮件时间，避免重复返回旧码
_STATE_LOCK = threading.Lock()
_LAST_SERVED_INTERNAL_DATE_MS: Dict[str, int] = {}


def _resolve_credentials_file() -> str:
    """解析 credentials 文件路径；若传入目录则自动拼接 credentials.json。"""
    configured = str(os.getenv("GMAIL_CREDENTIALS_FILE", CREDENTIALS_FILE) or "").strip()
    path = configured or CREDENTIALS_FILE
    if os.path.isdir(path):
        path = os.path.join(path, "credentials.json")
    return path


def _resolve_token_file() -> str:
    """解析 token 文件路径；若路径是目录，则自动落到目录下 token.json。"""
    configured = str(os.getenv("GMAIL_TOKEN_FILE", TOKEN_FILE) or "").strip()
    path = configured or TOKEN_FILE
    if os.path.isdir(path):
        path = os.path.join(path, "token.json")
    return path


def _save_token(creds: Credentials) -> None:
    token_file = _resolve_token_file()
    parent_dir = os.path.dirname(token_file)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)
    with open(token_file, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def _load_credentials() -> Credentials:
    creds: Optional[Credentials] = None
    token_file = _resolve_token_file()

    if os.path.exists(token_file) and os.path.isfile(token_file):
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds:
        raise RuntimeError(
            f"未找到 {token_file}，请先执行: python gmail_bridge.py --init-auth"
        )

    if creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
        _save_token(creds)

    if not creds.valid:
        raise RuntimeError("Gmail token 无效，请重新执行授权初始化")

    return creds


def _gmail_service():
    creds = _load_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def init_auth_once() -> None:
    """首次授权：弹浏览器登录 Google，生成 token.json"""
    credentials_file = _resolve_credentials_file()
    if not os.path.exists(credentials_file) or not os.path.isfile(credentials_file):
        raise FileNotFoundError(f"缺少 {credentials_file}")

    flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    print(f"[OK] 授权完成，已生成 {_resolve_token_file()}")


def _cleanup_oauth_states() -> None:
    now = time.time()
    expired_states: list[str] = []
    with _OAUTH_STATE_LOCK:
        for key, payload in _OAUTH_STATES.items():
            if now - float(payload.get("created_at", now)) > 600:
                expired_states.append(key)
        for key in expired_states:
            _OAUTH_STATES.pop(key, None)


def _register_oauth_state(state: str, redirect_uri: str, code_verifier: str = "") -> None:
    _cleanup_oauth_states()
    with _OAUTH_STATE_LOCK:
        _OAUTH_STATES[state] = {
            "redirect_uri": redirect_uri,
            "code_verifier": str(code_verifier or ""),
            "created_at": time.time(),
        }


def _pop_oauth_state(state: str) -> Dict[str, Any] | None:
    with _OAUTH_STATE_LOCK:
        return _OAUTH_STATES.pop(state, None)


def _build_web_oauth_flow(redirect_uri: str) -> Flow:
    credentials_file = _resolve_credentials_file()
    if not os.path.exists(credentials_file) or not os.path.isfile(credentials_file):
        raise FileNotFoundError(f"缺少 {credentials_file}")
    flow = Flow.from_client_secrets_file(
        credentials_file,
        SCOPES,
        autogenerate_code_verifier=True,
    )
    flow.redirect_uri = redirect_uri
    return flow


def _b64url_decode(data: str) -> str:
    if not data:
        return ""
    padding = "=" * (-len(data) % 4)
    raw = base64.urlsafe_b64decode(data + padding)
    return raw.decode("utf-8", errors="ignore")


def _extract_text_from_payload(payload: Dict[str, Any]) -> str:
    texts = []

    body = payload.get("body") or {}
    data = body.get("data")
    if data:
        texts.append(_b64url_decode(data))

    for part in payload.get("parts", []) or []:
        if not isinstance(part, dict):
            continue
        texts.append(_extract_text_from_payload(part))

    return "\n".join(t for t in texts if t)


def _extract_header(payload: Dict[str, Any], name: str) -> str:
    headers = payload.get("headers", []) or []
    target = name.lower()
    for h in headers:
        if str(h.get("name", "")).lower() == target:
            return str(h.get("value", "")).strip()
    return ""


def _extract_otp(text: str) -> str:
    patterns = [
        r"(?is)(?:verification\s*code|security\s*code|one[-\s]*time\s*(?:password|code)|验证码)[^0-9]{0,30}(\d{6})",
        r"(?<!\d)(\d{6})(?!\d)",
    ]
    content = str(text or "")
    for p in patterns:
        m = re.search(p, content)
        if m:
            return m.group(1)
    return ""


def _build_query(alias_to: str, kw: str, newer_than_hours: int) -> str:
    query = f"to:{alias_to} newer_than:{max(1, int(newer_than_hours))}h"
    if kw.strip():
        query += f' "{kw.strip()}"'
    return query


def _find_newest_otp(
    alias_to: str,
    kw: str,
    newer_than_hours: int = 24,
    max_messages: int = 10,
    max_age_minutes: int = 20,
) -> str:
    service = _gmail_service()
    query = _build_query(alias_to, kw, newer_than_hours)

    result = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=max_messages)
        .execute()
    )
    messages = result.get("messages", []) or []
    if not messages:
        return ""

    now_ms = int(time.time() * 1000)
    alias_key = alias_to.strip().lower()

    with _STATE_LOCK:
        last_served_ms = int(_LAST_SERVED_INTERNAL_DATE_MS.get(alias_key, 0))

    for msg in messages:
        msg_id = msg.get("id")
        if not msg_id:
            continue

        full = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )

        internal_date_ms = int(full.get("internalDate", "0") or 0)
        if internal_date_ms <= last_served_ms:
            continue

        # 过滤过旧邮件，降低串码概率
        if now_ms - internal_date_ms > max(1, max_age_minutes) * 60 * 1000:
            continue

        payload = full.get("payload", {}) or {}
        to_header = _extract_header(payload, "To").lower()
        subject = _extract_header(payload, "Subject")
        snippet = str(full.get("snippet", "") or "")
        body_text = _extract_text_from_payload(payload)
        merged = f"{subject}\n{snippet}\n{body_text}"

        # 优先确保邮件与目标 alias 相关
        if alias_key not in to_header and alias_key not in merged.lower():
            continue

        if kw.strip() and kw.strip().lower() not in merged.lower():
            continue

        code = _extract_otp(merged)
        if not code:
            continue

        with _STATE_LOCK:
            _LAST_SERVED_INTERNAL_DATE_MS[alias_key] = max(
                _LAST_SERVED_INTERNAL_DATE_MS.get(alias_key, 0),
                internal_date_ms,
            )
        return code

    return ""


@app.get("/health", response_class=PlainTextResponse)
def health() -> str:
    return "ok"


@app.get("/auth/status")
def auth_status() -> dict[str, Any]:
    token_file = _resolve_token_file()
    if not os.path.exists(token_file) or not os.path.isfile(token_file):
        return {"authorized": False, "message": f"未检测到 {token_file}"}
    try:
        _load_credentials()
        return {"authorized": True, "message": "已授权"}
    except Exception as exc:
        return {"authorized": False, "message": str(exc)}


@app.get("/auth/start")
def auth_start(request: Request, redirect_uri: str = ""):
    target_redirect_uri = str(redirect_uri or "").strip() or str(request.url_for("auth_callback"))
    try:
        flow = _build_web_oauth_flow(target_redirect_uri)
        authorization_url, state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
            code_challenge_method="S256",
        )
        _register_oauth_state(
            state=state,
            redirect_uri=target_redirect_uri,
            code_verifier=str(getattr(flow, "code_verifier", "") or ""),
        )
        return RedirectResponse(authorization_url, status_code=302)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"启动 Google 授权失败: {exc}") from exc


@app.get("/auth/callback", response_class=HTMLResponse, name="auth_callback")
def auth_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
    if error:
        return HTMLResponse(
            f"<html><body><h3>Google 授权失败: {error}</h3><p>请关闭本页面后重试。</p></body></html>",
            status_code=400,
        )

    payload = _pop_oauth_state(state)
    if not payload:
        return HTMLResponse(
            "<html><body><h3>授权状态已失效</h3><p>请返回系统 UI 重新发起授权。</p></body></html>",
            status_code=400,
        )

    redirect_uri = str(payload.get("redirect_uri") or "").strip()
    code_verifier = str(payload.get("code_verifier") or "").strip()
    if not redirect_uri or not code:
        return HTMLResponse(
            "<html><body><h3>授权参数不完整</h3><p>请返回系统 UI 重新发起授权。</p></body></html>",
            status_code=400,
        )
    if not code_verifier:
        return HTMLResponse(
            "<html><body><h3>授权会话缺失 code_verifier</h3><p>请返回系统 UI 重新点击 Google 登录授权。</p></body></html>",
            status_code=400,
        )

    try:
        flow = _build_web_oauth_flow(redirect_uri)
        if code_verifier:
            flow.code_verifier = code_verifier
        flow.fetch_token(code=code, code_verifier=code_verifier)
        _save_token(flow.credentials)
    except Exception as exc:
        return HTMLResponse(
            f"<html><body><h3>保存授权失败: {exc}</h3><p>请关闭本页面后重试。</p></body></html>",
            status_code=500,
        )

    return HTMLResponse(
        """
        <html>
          <body>
            <h3>Google 授权成功</h3>
            <p>token.json 已写入，请返回系统继续操作。</p>
            <script>
              setTimeout(function () { window.close(); }, 1200);
            </script>
          </body>
        </html>
        """,
    )


@app.get("/otp", response_class=PlainTextResponse)
def otp(
    to: str = Query(..., description="目标邮箱（可带 +alias）"),
    kw: str = Query("", description="关键词，例如 OpenAI / ChatGPT"),
    newer_than_hours: int = Query(24, ge=1, le=72),
    max_age_minutes: int = Query(20, ge=1, le=60),
) -> str:
    alias_to = str(to or "").strip().lower()
    if "@" not in alias_to:
        raise HTTPException(status_code=400, detail="to 参数不是合法邮箱")

    try:
        code = _find_newest_otp(
            alias_to=alias_to,
            kw=kw,
            newer_than_hours=newer_than_hours,
            max_messages=10,
            max_age_minutes=max_age_minutes,
        )
        return code
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"otp bridge error: {exc}") from exc


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-auth", action="store_true", help="首次授权并生成 token.json")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()

    if args.init_auth:
        init_auth_once()
    else:
        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port)
