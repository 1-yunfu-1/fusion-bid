"""本地 LLM 密钥与多组 API 配置（gitignore，禁止提交 Git）.

- 支持多 profile：名称 + base_url + api_key + 可选 model
- 优先级：当前激活 profile 的 key > 遗留 api_key 字段 > 环境变量 LLM_API_KEY
- 任何对外 API 不得返回完整明文 Key，仅可返回是否已配置与脱敏提示
"""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import PROJECT_ROOT, get_settings

logger = logging.getLogger(__name__)
_lock = threading.Lock()

_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


def secrets_path() -> Path:
    settings = get_settings()
    path = settings.data_dir / "llm_secrets.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def load_secrets() -> dict[str, Any]:
    path = secrets_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:  # noqa: BLE001
        logger.warning("llm_secrets.json unreadable")
        return {}


def _write_secrets(payload: dict[str, Any]) -> None:
    path = secrets_path()
    with _lock:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_profiles(raw: dict[str, Any]) -> dict[str, Any]:
    """保证 profiles 列表结构；迁移遗留单 key."""
    data = dict(raw or {})
    profiles = data.get("profiles")
    if not isinstance(profiles, list):
        profiles = []

    # 迁移：旧版只有 api_key
    legacy_key = (data.get("api_key") or "").strip()
    if legacy_key and not profiles:
        profiles = [
            {
                "id": "default",
                "name": "默认",
                "base_url": "",
                "api_key": legacy_key,
                "model": "",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        ]
        data["active_profile_id"] = "default"
        # 保留 legacy 字段兼容，同步到 active
        data["api_key"] = legacy_key

    cleaned: list[dict[str, Any]] = []
    for p in profiles:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id") or _new_id())
        cleaned.append(
            {
                "id": pid,
                "name": str(p.get("name") or "未命名").strip()[:64] or "未命名",
                "base_url": str(p.get("base_url") or "").strip().rstrip("/"),
                "api_key": str(p.get("api_key") or "").strip(),
                "model": str(p.get("model") or "").strip(),
                "created_at": str(p.get("created_at") or _now_iso()),
                "updated_at": str(p.get("updated_at") or _now_iso()),
            }
        )
    data["profiles"] = cleaned

    active = data.get("active_profile_id")
    if cleaned:
        ids = {p["id"] for p in cleaned}
        if not active or active not in ids:
            data["active_profile_id"] = cleaned[0]["id"]
    else:
        data["active_profile_id"] = None

    # 同步遗留 api_key = 当前激活 profile 的 key（便于旧代码路径）
    active_p = _find_profile(data, data.get("active_profile_id"))
    if active_p:
        ak = (active_p.get("api_key") or "").strip()
        if ak:
            data["api_key"] = ak
        else:
            data.pop("api_key", None)
    elif not cleaned:
        if not (data.get("api_key") or "").strip():
            data.pop("api_key", None)

    return data


def _find_profile(data: dict[str, Any], profile_id: str | None) -> dict[str, Any] | None:
    if not profile_id:
        return None
    for p in data.get("profiles") or []:
        if isinstance(p, dict) and p.get("id") == profile_id:
            return p
    return None


def _public_profile(p: dict[str, Any], *, active_id: str | None) -> dict[str, Any]:
    key = (p.get("api_key") or "").strip()
    return {
        "id": p["id"],
        "name": p.get("name") or "未命名",
        "base_url": p.get("base_url") or "",
        "model": p.get("model") or "",
        "key_configured": bool(key),
        "key_hint": mask_api_key(key) if key else "",
        "is_active": p["id"] == active_id,
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
    }


def list_profiles() -> dict[str, Any]:
    data = _normalize_profiles(load_secrets())
    active = data.get("active_profile_id")
    items = [_public_profile(p, active_id=active) for p in data.get("profiles") or []]
    return {
        "ok": True,
        "active_profile_id": active,
        "count": len(items),
        "profiles": items,
        **key_status(),
    }


def get_active_profile() -> dict[str, Any] | None:
    data = _normalize_profiles(load_secrets())
    return _find_profile(data, data.get("active_profile_id"))


def upsert_profile(
    *,
    profile_id: str | None = None,
    name: str,
    base_url: str,
    api_key: str | None = None,
    model: str | None = None,
    activate: bool = False,
    clear_key: bool = False,
) -> dict[str, Any]:
    """新建或更新 profile。更新时 api_key 为空表示不改动密钥."""
    name = (name or "").strip()[:64]
    if not name:
        raise ValueError("配置名称不能为空")
    base_url = (base_url or "").strip().rstrip("/")
    if base_url and not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise ValueError("Base URL 需以 http:// 或 https:// 开头")

    data = _normalize_profiles(load_secrets())
    profiles: list[dict[str, Any]] = list(data.get("profiles") or [])

    if profile_id:
        if not _ID_RE.match(profile_id):
            raise ValueError("非法配置 id")
        existing = _find_profile(data, profile_id)
        if not existing:
            raise ValueError(f"配置不存在: {profile_id}")
        existing["name"] = name
        existing["base_url"] = base_url
        if model is not None:
            existing["model"] = (model or "").strip()
        if clear_key:
            existing["api_key"] = ""
        elif api_key is not None and str(api_key).strip():
            key = str(api_key).strip()
            if len(key) < 8:
                raise ValueError("API Key 过短，请检查")
            existing["api_key"] = key
        existing["updated_at"] = _now_iso()
        # write back
        profiles = [existing if p.get("id") == profile_id else p for p in profiles]
        target_id = profile_id
    else:
        key = (api_key or "").strip()
        if not key:
            raise ValueError("新建配置时必须填写 API Key")
        if len(key) < 8:
            raise ValueError("API Key 过短，请检查")
        target_id = _new_id()
        profiles.append(
            {
                "id": target_id,
                "name": name,
                "base_url": base_url,
                "api_key": key,
                "model": (model or "").strip(),
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
            }
        )

    data["profiles"] = profiles
    if activate or not data.get("active_profile_id"):
        data["active_profile_id"] = target_id
    data = _normalize_profiles(data)
    _write_secrets(data)
    logger.info("llm profile saved id=%s (key not logged)", target_id)
    return {
        "ok": True,
        "profile": _public_profile(
            _find_profile(data, target_id) or {},  # type: ignore[arg-type]
            active_id=data.get("active_profile_id"),
        ),
        "active_profile_id": data.get("active_profile_id"),
        "profiles": [
            _public_profile(p, active_id=data.get("active_profile_id"))
            for p in data.get("profiles") or []
        ],
        **key_status(),
    }


def delete_profile(profile_id: str) -> dict[str, Any]:
    data = _normalize_profiles(load_secrets())
    profiles = [p for p in (data.get("profiles") or []) if p.get("id") != profile_id]
    if len(profiles) == len(data.get("profiles") or []):
        raise ValueError(f"配置不存在: {profile_id}")
    data["profiles"] = profiles
    if data.get("active_profile_id") == profile_id:
        data["active_profile_id"] = profiles[0]["id"] if profiles else None
    data = _normalize_profiles(data)
    if not profiles and not (data.get("api_key") or "").strip():
        path = secrets_path()
        with _lock:
            if path.exists():
                path.unlink()
        logger.info("llm secrets file removed (no profiles)")
        return {"ok": True, "deleted": profile_id, "profiles": [], **key_status()}
    _write_secrets(data)
    logger.info("llm profile deleted id=%s", profile_id)
    return {
        "ok": True,
        "deleted": profile_id,
        "active_profile_id": data.get("active_profile_id"),
        "profiles": [
            _public_profile(p, active_id=data.get("active_profile_id"))
            for p in data.get("profiles") or []
        ],
        **key_status(),
    }


def activate_profile(profile_id: str) -> dict[str, Any]:
    data = _normalize_profiles(load_secrets())
    p = _find_profile(data, profile_id)
    if not p:
        raise ValueError(f"配置不存在: {profile_id}")
    data["active_profile_id"] = profile_id
    data = _normalize_profiles(data)
    _write_secrets(data)
    logger.info("llm profile activated id=%s", profile_id)
    return {
        "ok": True,
        "active_profile_id": profile_id,
        "profile": _public_profile(p, active_id=profile_id),
        "profiles": [
            _public_profile(x, active_id=profile_id) for x in data.get("profiles") or []
        ],
        **key_status(),
        "message": f"已切换到配置：{p.get('name')}",
    }


def save_api_key(api_key: str) -> dict[str, Any]:
    """兼容旧接口：保存到当前激活 profile；若无则新建「默认」."""
    key = (api_key or "").strip()
    if not key:
        raise ValueError("API Key 不能为空")
    if len(key) < 8:
        raise ValueError("API Key 过短，请检查")

    data = _normalize_profiles(load_secrets())
    active = _find_profile(data, data.get("active_profile_id"))
    if active:
        return upsert_profile(
            profile_id=active["id"],
            name=active.get("name") or "默认",
            base_url=active.get("base_url") or "",
            api_key=key,
            model=active.get("model") or "",
            activate=True,
        )
    return upsert_profile(
        name="默认",
        base_url="",
        api_key=key,
        model="",
        activate=True,
    )


def clear_api_key() -> dict[str, Any]:
    """兼容旧接口：清除当前激活 profile 的 key；若无 profile 则清遗留字段."""
    data = _normalize_profiles(load_secrets())
    active = _find_profile(data, data.get("active_profile_id"))
    if active:
        return upsert_profile(
            profile_id=active["id"],
            name=active.get("name") or "默认",
            base_url=active.get("base_url") or "",
            model=active.get("model") or "",
            clear_key=True,
            activate=True,
        )
    data.pop("api_key", None)
    path = secrets_path()
    with _lock:
        if data.get("profiles"):
            _write_secrets(data)
        elif path.exists():
            path.unlink()
    logger.info("llm api key cleared from local secrets file")
    return key_status()


def get_llm_api_key() -> str:
    """运行时使用的 API Key：激活 profile > 遗留字段 > 环境变量."""
    data = _normalize_profiles(load_secrets())
    active = _find_profile(data, data.get("active_profile_id"))
    if active:
        local = (active.get("api_key") or "").strip()
        if local:
            return local
    legacy = (data.get("api_key") or "").strip()
    if legacy:
        return legacy
    return (get_settings().llm_api_key or "").strip()


def get_active_api_base_url() -> str | None:
    """激活 profile 的 base_url（空则返回 None，由 runtime/env 兜底）."""
    active = get_active_profile()
    if not active:
        return None
    url = (active.get("base_url") or "").strip().rstrip("/")
    return url or None


def get_active_api_model() -> str | None:
    active = get_active_profile()
    if not active:
        return None
    model = (active.get("model") or "").strip()
    return model or None


def mask_api_key(key: str) -> str:
    key = (key or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


def key_status() -> dict[str, Any]:
    key = get_llm_api_key()
    source = "none"
    data = _normalize_profiles(load_secrets())
    active = _find_profile(data, data.get("active_profile_id"))
    if active and (active.get("api_key") or "").strip():
        source = "profile"
    elif (data.get("api_key") or "").strip():
        source = "local_secrets"
    elif (get_settings().llm_api_key or "").strip():
        source = "env"

    path = secrets_path()
    try:
        rel = str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        rel = str(path)

    profile_name = (active or {}).get("name") if active else None
    profile_id = data.get("active_profile_id")
    profile_count = len(data.get("profiles") or [])

    if source == "profile":
        msg = f"已配置 API Key（配置组：{profile_name or profile_id}）"
    elif source == "local_secrets":
        msg = "已配置 API Key（本地文件）"
    elif source == "env":
        msg = "已配置 API Key（环境变量 LLM_API_KEY）"
    else:
        msg = "未配置 API Key，请在设置页添加 API 配置组或配置环境变量"

    return {
        "configured": bool(key),
        "source": source,
        "hint": mask_api_key(key) if key else "",
        "secrets_path": rel,
        "active_profile_id": profile_id,
        "active_profile_name": profile_name,
        "profile_count": profile_count,
        "message": msg,
    }
