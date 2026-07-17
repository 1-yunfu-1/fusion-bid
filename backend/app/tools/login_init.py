"""登录初始化 CLI：可见浏览器手动登录并保存 storage state.

用法（在 backend 目录）:
  python -m app.tools.login_init
  python -m app.tools.login_init --url https://example.com/login
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 保证可导入 app
_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.browser.session import BrowserNotAvailableError, interactive_login, state_file_path
from app.core.config import get_settings


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="FusionBid 登录态初始化（手动登录）")
    parser.add_argument(
        "--url",
        default=settings.login_source_login_url,
        help="登录页 URL",
    )
    parser.add_argument(
        "--state",
        default=None,
        help="storage state 保存路径（默认 data/browser_states/ 下配置文件名）",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无头模式（不推荐，无法手动过验证码）",
    )
    parser.add_argument(
        "--wait",
        type=int,
        default=300,
        help="无终端输入时自动保存前的等待秒数（默认 300）",
    )
    args = parser.parse_args(argv)

    state = Path(args.state) if args.state else state_file_path()
    try:
        path = interactive_login(
            login_url=args.url,
            state_path=state,
            headless=args.headless,
            wait_seconds=args.wait,
        )
    except BrowserNotAvailableError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"登录初始化失败: {exc}", file=sys.stderr)
        return 1

    print(f"已保存登录状态到: {path}")
    print("提示: 该文件已在 .gitignore 中，请勿提交到 Git。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
