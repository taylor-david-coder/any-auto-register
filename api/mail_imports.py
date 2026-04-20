from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query

from services.mail_imports import (
    MailImportBatchDeleteRequest,
    MailImportDeleteRequest,
    MailImportExecuteRequest,
    MailImportSnapshotRequest,
    mail_import_registry,
)

router = APIRouter(prefix="/mail-imports", tags=["mail-imports"])


@router.get("/providers")
def list_mail_import_providers():
    return {"items": mail_import_registry.descriptors()}


@router.get("/snapshot")
def get_mail_import_snapshot(
    provider_type: str = Query(alias="type"),
    pool_dir: str = "",
    pool_file: str = "",
    preview_limit: int = 100,
):
    try:
        strategy = mail_import_registry.get(provider_type)
        request = MailImportSnapshotRequest(
            type=strategy.descriptor.type,
            pool_dir=pool_dir,
            pool_file=pool_file,
            preview_limit=preview_limit,
        )
        return strategy.get_snapshot(request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("")
def execute_mail_import(body: MailImportExecuteRequest):
    try:
        strategy = mail_import_registry.get(body.type)
        return strategy.execute(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/delete")
def delete_mail_import_item(body: MailImportDeleteRequest):
    try:
        strategy = mail_import_registry.get(body.type)
        return strategy.delete(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/batch-delete")
def batch_delete_mail_import_items(body: MailImportBatchDeleteRequest):
    try:
        strategy = mail_import_registry.get(body.type)
        return strategy.batch_delete(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/gmail-alias-template")
def get_gmail_alias_template(
    base_email: str = Query(..., description="Gmail 主邮箱地址（例如 name@gmail.com）"),
    count: int = Query(10, ge=1, le=500, description="生成条数"),
    start_index: int = Query(1, ge=1, le=999999, description="起始序号"),
    keyword: str = Query("OpenAI", description="取码关键词"),
    bridge_base_url: str = Query(
        "http://gmail-bridge:9090",
        description="Gmail Bridge 基础地址（同 compose 网络建议使用服务名）",
    ),
):
    normalized_email = str(base_email or "").strip().lower()
    if "@" not in normalized_email:
        raise HTTPException(status_code=400, detail="base_email 不是合法邮箱")

    local, domain = normalized_email.split("@", 1)
    if domain != "gmail.com" or not local:
        raise HTTPException(status_code=400, detail="当前仅支持 gmail.com 邮箱")

    base_url = str(bridge_base_url or "").strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="bridge_base_url 必须以 http:// 或 https:// 开头")

    safe_keyword = str(keyword or "").strip() or "OpenAI"
    lines: list[str] = []
    for idx in range(start_index, start_index + count):
        alias = f"{local}+u{idx:03d}@{domain}"
        encoded_alias = quote(alias, safe="")
        line = (
            f"{alias}----"
            f"{base_url}/otp?to={encoded_alias}&kw={quote(safe_keyword, safe='')}"
        )
        lines.append(line)

    return {
        "base_email": normalized_email,
        "count": count,
        "start_index": start_index,
        "keyword": safe_keyword,
        "bridge_base_url": base_url,
        "lines": lines,
        "content": "\n".join(lines),
    }
