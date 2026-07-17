Set sh = CreateObject("WScript.Shell")
' Open interactive console for Playwright login (ASCII-only, encoding-safe)
cmd = "cmd.exe /k ""cd /d F:\feishu\backend && .venv\Scripts\python.exe -m app.tools.login_init --wait 600"""
sh.Run cmd, 1, False
