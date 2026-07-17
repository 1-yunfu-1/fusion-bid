"""登录态数据源导出（兼容旧占位名）."""

from __future__ import annotations

from app.sources.login_portal_source import LoginPortalSource

# 主实现
LoginSource = LoginPortalSource


class LoginSourcePlaceholder(LoginPortalSource):
    """兼容旧测试/导入名；实际为登录门户实现."""

    pass
