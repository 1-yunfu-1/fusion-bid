"""Tests for the isolated public-site browser lifecycle."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.browser import managed_public as managed_module
from app.browser import pdf_detail as pdf_detail_module
from app.browser.managed_public import ManagedPublicBrowser
from app.browser.pdf_detail import PublicPdfDetail


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts = [object()]
        self.connected = True
        self.closed = False

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.closed = True
        self.connected = False


class _FakeProcess:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, _timeout=None):
        return self.returncode


def test_pdf_title_identity_accepts_official_document_without_announcement_suffix():
    assert pdf_detail_module._pdf_content_matches_title(
        "服务器、数据库、数据库集群软件招标公告",
        "1.招标条件 本招标项目：服务器、数据库、数据库集群软件已批准建设",
    )
    assert pdf_detail_module._pdf_content_matches_title(
        "华能霞浦核电项目设备国际招标澄清或变更公告(9)",
        "华能霞浦核电项目设备 招标编号：XYZ",
    )


def test_pdf_title_identity_rejects_unrelated_document():
    assert not pdf_detail_module._pdf_content_matches_title(
        "服务器、数据库、数据库集群软件招标公告",
        "另一个项目的招标公告及资格要求",
    )


@pytest.mark.asyncio
async def test_pdf_collection_rejects_non_official_origin_before_navigation():
    class FakePage:
        goto_called = False

        async def goto(self, *_args, **_kwargs):
            self.goto_called = True

    page = FakePage()
    result = await pdf_detail_module._collect_public_pdf_detail(
        page,
        detail_url="https://example.com/#/bulletinDetail?uuid=" + "a" * 32,
        expected_id="a" * 32,
        expected_title="测试公告",
    )

    assert result.status == "failed"
    assert result.failure_reason == "invalid_detail_origin"
    assert page.goto_called is False


@pytest.mark.asyncio
async def test_launch_uses_isolated_loopback_profile_without_evasion_flags(
    tmp_path, monkeypatch
):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "cebpub_managed_profile"
    browser.runtime_file = tmp_path / "cebpub_managed_runtime.json"
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")
    process = _FakeProcess(41001)
    launches: list[list[str]] = []

    monkeypatch.setattr(
        managed_module, "_browser_candidates", lambda: [("chrome", executable)]
    )
    monkeypatch.setattr(managed_module, "_free_loopback_port", lambda: 41237)

    def fake_popen(args, **_kwargs):
        launches.append([str(value) for value in args])
        return process

    monkeypatch.setattr(managed_module.subprocess, "Popen", fake_popen)

    async def ready(_port, timeout_seconds=20):
        return True

    async def connect(_port):
        browser._browser = _FakeBrowser()

    monkeypatch.setattr(browser, "_cdp_ready", ready)
    monkeypatch.setattr(browser, "_connect", connect)

    reused = await browser.ensure_started()

    assert reused is False
    assert len(launches) == 1
    args = launches[0]
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=41237" in args
    assert f"--user-data-dir={browser.profile_dir.resolve()}" in args
    assert not any("user-agent" in arg.lower() for arg in args)
    assert "--enable-automation" not in args
    assert not any("automationcontrolled" in arg.lower() for arg in args)
    runtime = json.loads(browser.runtime_file.read_text(encoding="utf-8"))
    assert runtime["pid"] == process.pid
    assert runtime["engine"] == "chrome"

    await browser.shutdown()
    assert process.terminated is True
    assert process.killed is False
    assert not browser.runtime_file.exists()


@pytest.mark.asyncio
async def test_start_falls_back_from_chrome_to_edge(tmp_path, monkeypatch):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.runtime_file = tmp_path / "runtime.json"
    chrome = tmp_path / "chrome.exe"
    edge = tmp_path / "msedge.exe"
    chrome.write_bytes(b"")
    edge.write_bytes(b"")
    processes = [_FakeProcess(42001), _FakeProcess(42002)]
    ports = iter([42011, 42012])
    launches = []

    monkeypatch.setattr(
        managed_module,
        "_browser_candidates",
        lambda: [("chrome", chrome), ("msedge", edge)],
    )
    monkeypatch.setattr(managed_module, "_free_loopback_port", lambda: next(ports))

    def fake_popen(args, **_kwargs):
        launches.append(args)
        return processes[len(launches) - 1]

    monkeypatch.setattr(managed_module.subprocess, "Popen", fake_popen)

    async def ready(port, timeout_seconds=20):
        return port == 42012

    async def connect(_port):
        browser._browser = _FakeBrowser()

    monkeypatch.setattr(browser, "_cdp_ready", ready)
    monkeypatch.setattr(browser, "_connect", connect)

    assert await browser.ensure_started() is False
    assert len(launches) == 2
    assert processes[0].terminated is True
    assert browser.status()["engine"] == "msedge"
    await browser.shutdown()


@pytest.mark.asyncio
async def test_operation_queue_serializes_public_page_work(tmp_path, monkeypatch):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.runtime_file = tmp_path / "runtime.json"
    browser._browser = _FakeBrowser()
    browser._engine = "chrome"
    browser._state = "ready"

    async def already_started():
        return True

    monkeypatch.setattr(browser, "ensure_started", already_started)
    active = 0
    maximum_active = 0
    order: list[str] = []

    async def worker(name: str):
        nonlocal active, maximum_active
        async with browser.acquire() as lease:
            assert lease.context is browser._browser.contexts[0]
            active += 1
            maximum_active = max(maximum_active, active)
            order.append(f"start-{name}")
            await asyncio.sleep(0.02)
            order.append(f"end-{name}")
            active -= 1

    await asyncio.gather(worker("a"), worker("b"))

    assert maximum_active == 1
    assert order in (
        ["start-a", "end-a", "start-b", "end-b"],
        ["start-b", "end-b", "start-a", "end-a"],
    )
    assert browser.status()["state"] == "ready"


@pytest.mark.asyncio
async def test_previous_runtime_requires_exact_process_identity(tmp_path, monkeypatch):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.profile_dir.mkdir()
    browser.runtime_file = tmp_path / "runtime.json"
    executable = tmp_path / "chrome.exe"
    other = tmp_path / "unknown.exe"
    executable.write_bytes(b"")
    other.write_bytes(b"")
    browser.runtime_file.write_text(
        json.dumps(
            {
                "pid": 43001,
                "port": 43002,
                "engine": "chrome",
                "executable": str(executable),
                "profile_dir": str(browser.profile_dir),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        managed_module, "_browser_candidates", lambda: [("chrome", executable.resolve())]
    )
    monkeypatch.setattr(managed_module, "_process_executable", lambda _pid: other)

    assert await browser._try_attach_previous() is False
    assert not browser.runtime_file.exists()


@pytest.mark.asyncio
async def test_application_restart_reuses_verified_managed_browser(tmp_path, monkeypatch):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.profile_dir.mkdir()
    browser.runtime_file = tmp_path / "runtime.json"
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"")
    browser.runtime_file.write_text(
        json.dumps(
            {
                "pid": 43501,
                "port": 43502,
                "engine": "chrome",
                "executable": str(executable),
                "profile_dir": str(browser.profile_dir),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        managed_module,
        "_browser_candidates",
        lambda: [("chrome", executable.resolve())],
    )
    monkeypatch.setattr(
        managed_module, "_process_executable", lambda _pid: executable.resolve()
    )

    async def ready(port, timeout_seconds=20):
        assert port == 43502
        return True

    async def connect(port):
        assert port == 43502
        browser._browser = _FakeBrowser()

    monkeypatch.setattr(browser, "_cdp_ready", ready)
    monkeypatch.setattr(browser, "_connect", connect)

    assert await browser.ensure_started() is True
    assert browser.status()["state"] == "ready"
    assert browser.status()["engine"] == "chrome"
    assert browser._pid == 43501


def test_public_status_never_exposes_port_or_local_profile(tmp_path):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "private-profile"
    browser.profile_dir.mkdir()
    (browser.profile_dir / "Local State").write_text("{}", encoding="utf-8")
    browser._port = 44001
    browser._state = "ready"

    status = browser.status()

    assert status == {
        "state": "ready",
        "engine": None,
        "profile_ready": True,
        "last_error": None,
    }
    assert "44001" not in str(status)
    assert str(browser.profile_dir) not in str(status)


def test_public_error_redacts_local_path_and_loopback_port():
    message = managed_module._public_error_message(
        r"failed C:\Users\example\profile at 127.0.0.1:44001"
    )

    assert "C:\\Users" not in message
    assert "44001" not in message


@pytest.mark.asyncio
async def test_pdf_collection_restarts_managed_browser_once_after_crash(monkeypatch):
    class FakePage:
        async def close(self):
            return None

    class FakeBroker:
        def __init__(self):
            self.acquire_count = 0
            self.shutdown_count = 0

        @asynccontextmanager
        async def acquire(self, *, interactive=False):
            self.acquire_count += 1
            attempt = self.acquire_count

            class Context:
                async def new_page(self):
                    if attempt == 1:
                        raise RuntimeError("browser process exited")
                    return FakePage()

            yield SimpleNamespace(
                context=Context(), reused=attempt > 1, engine="chrome"
            )

        @property
        def is_connected(self):
            return True

        def status(self):
            return {"state": "ready"}

        def mark_needs_verification(self):
            return None

        async def shutdown(self):
            self.shutdown_count += 1

    broker = FakeBroker()
    monkeypatch.setattr(
        managed_module, "get_managed_public_browser", lambda: broker
    )

    async def collect(_page, **kwargs):
        return PublicPdfDetail(
            status="full",
            detail_url=kwargs["detail_url"],
            content_format="pdf_text",
            clean_content="【第1页】\n招标人：测试单位",
            pages=[{"page": 1, "text": "招标人：测试单位"}],
        )

    monkeypatch.setattr(pdf_detail_module, "_collect_public_pdf_detail", collect)

    result = await pdf_detail_module._fetch_managed_public_pdf_detail(
        detail_url="https://ctbpsp.com/#/bulletinDetail?uuid=" + "a" * 32,
        expected_id="a" * 32,
        expected_title="测试公告",
        timeout_ms=60_000,
        headless=True,
    )

    assert result.status == "full"
    assert result.acquisition_mode == "managed_chrome"
    assert result.browser_reused is True
    assert broker.acquire_count == 2
    assert broker.shutdown_count == 1
