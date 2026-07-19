"""Tests for the isolated public-site browser lifecycle."""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.browser import managed_public as managed_module
from app.browser import pdf_detail as pdf_detail_module
from app.browser.managed_public import ManagedPublicBrowser
from app.browser.pdf_detail import PublicPdfDetail


class _FakePage:
    def __init__(self) -> None:
        self.closed = False

    def is_closed(self) -> bool:
        return self.closed

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self) -> None:
        self.pages = [_FakePage()]

    async def new_page(self):
        page = _FakePage()
        self.pages.append(page)
        return page


class _FakeBrowser:
    def __init__(self) -> None:
        self.contexts = [_FakeContext()]
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
    assert pdf_detail_module._pdf_content_matches_title(
        "某核电项目调节阀设备采购（第二次）国际招标公告(2)",
        "招标项目名称:某核电项目调节阀设备采购（第二次）",
    )


def test_pdf_title_identity_rejects_unrelated_document():
    assert not pdf_detail_module._pdf_content_matches_title(
        "服务器、数据库、数据库集群软件招标公告",
        "另一个项目的招标公告及资格要求",
    )


def test_pdf_identity_accepts_two_ordered_title_spans_with_one_bad_connector():
    signals = pdf_detail_module._pdf_identity_signals(
        "云服务器租赁机DNS解析",
        (
            "拟采购云服务器租赁，现开展询比价工作。"
            "物资包括香港云服务器租赁和DNS企业版解析基础防御版。"
        ),
    )

    assert signals["accepted"] is True
    assert signals["method"] == "ordered_title_spans"
    assert signals["title_span_coverage"] >= 0.75


def test_pdf_identity_does_not_accept_one_generic_title_fragment():
    signals = pdf_detail_module._pdf_identity_signals(
        "云服务器租赁机DNS解析",
        "另一采购项目需要服务器设备，但没有对应项目名称或采购内容。",
    )

    assert signals["accepted"] is False
    assert signals["title_span_match"] is False


@pytest.mark.parametrize(
    ("outer_title", "pdf_text", "project_code"),
    [
        (
            "华能霞浦核电项目压水堆一期工程高位排气阀设备采购项目重新招标澄清或变更公告(1)",
            "项目名称：华能霞浦核电项目压水堆一期工程高位排气阀设备采购项目。招标编号：0739-264Z0008CNPE",
            "0739-264Z0008CNPE000",
        ),
        (
            "供销物流系统无卡化等项目服务器、电脑招标公告（电子标）",
            "项目名称：供销物流系统无卡化等项目服务器、电脑。招标人：安徽海螺信息技术工程有限责任公司",
            None,
        ),
        (
            "中国融通集团信息技术有限公司纪检项目算力服务器采购项目招标公告",
            "本项目 纪检项目算力服务器采购项目（项目编号：GKZB07202607150001），招标人为中国融通集团信息技术有限公司。",
            "M1101085050001211001",
        ),
        (
            "中国融通集团信息技术有限公司2025年纪检项目测试服务器采购项目询比采购公告",
            "本项目 2025年纪检项目测试服务器采购项目（项目编号：XBCG07202607150001），采购人为中国融通集团信息技术有限公司。",
            "M1101085050001205001",
        ),
        (
            "中石化胜利石油工程有限公司2026年胜利工程钻井院存储设备和计算机配件采购方案内存条/工作站/服务器 32GB DDR5 6000MHZ招标公告",
            "本招标项目 2026年胜利工程钻井院存储设备和计算机配件采购方案内存条/工作站/服务器 32GB DDR5 6000MHZ（招标编号：NWZ260715-3411-129552），招标人为中石化胜利石油工程有限公司。",
            "WZ260715-3411-129552",
        ),
        (
            "服务器、工作站、电脑临时采购项目项目公告",
            "服务器、工作站、电脑临时采购项目已批准，采购人为中煤科工西安研究院（集团）有限公司。采购编号：XBXM-202607-10-0429",
            "D1100005299XB2607429",
        ),
    ],
)
def test_pdf_identity_accepts_real_title_wrappers(outer_title, pdf_text, project_code):
    signals = pdf_detail_module._pdf_identity_signals(
        outer_title, pdf_text, expected_project_code=project_code
    )
    assert signals["accepted"] is True
    assert signals["method"] != "unverified"


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
async def test_pdf_collection_uses_blank_navigation_boundary_before_hash_route():
    class Locator:
        async def inner_text(self, **_kwargs):
            return "测试服务器采购项目招标公告"

    class Page:
        def __init__(self):
            self.gotos = []

        async def goto(self, url, **_kwargs):
            self.gotos.append(url)

        async def wait_for_selector(self, *_args, **_kwargs):
            raise TimeoutError

        def locator(self, _selector):
            return Locator()

    page = Page()
    detail_url = (
        "https://ctbpsp.com/#/bulletinDetail?uuid=" + "a" * 32
    )
    result = await pdf_detail_module._collect_public_pdf_detail(
        page,
        detail_url=detail_url,
        expected_id="a" * 32,
        expected_title="测试服务器采购项目招标公告",
    )

    assert page.gotos == ["about:blank", detail_url]
    assert result.failure_reason == "pdf_not_loaded"


@pytest.mark.asyncio
async def test_blank_spa_without_challenge_is_not_reported_as_verification():
    class Locator:
        async def inner_text(self, **_kwargs):
            return "首页\n联系我们"

    class Page:
        async def goto(self, *_args, **_kwargs):
            return None

        async def wait_for_selector(self, *_args, **_kwargs):
            raise TimeoutError

        def locator(self, _selector):
            return Locator()

    result = await pdf_detail_module._collect_public_pdf_detail(
        Page(),
        detail_url="https://ctbpsp.com/#/bulletinDetail?uuid=" + "d" * 32,
        expected_id="d" * 32,
        expected_title="测试服务器采购项目招标公告",
    )

    assert result.status == "metadata_only"
    assert result.failure_reason == "outer_detail_unavailable"
    assert result.failure_reason != "verification_required"


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_renders_loaded_pdfjs_document_without_dom_canvas(
    monkeypatch,
):
    class Frame:
        def __init__(self):
            self.render_calls = 0

        def locator(self, _selector):
            return object()

        async def evaluate(self, expression, *args):
            if "numPages" in expression and not args:
                return 1
            self.render_calls += 1
            assert args == (1,)
            return base64.b64encode(b"rendered-png").decode()

    frame = Frame()
    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=object))
    monkeypatch.setattr(
        pdf_detail_module,
        "_recognise_image_bytes",
        lambda data: "采购人：测试单位" if data == b"rendered-png" else "",
    )

    pages = await pdf_detail_module._ocr_rendered_pages(frame, page_numbers={1})

    assert pages == [{"page": 1, "text": "采购人：测试单位", "method": "ocr"}]
    assert frame.render_calls == 1


def test_local_pdf_parser_uses_ocr_for_a_textless_page(monkeypatch):
    fitz = pytest.importorskip("fitz")
    document = fitz.open()
    document.new_page(width=320, height=240)
    data = document.tobytes()
    document.close()
    monkeypatch.setattr(
        pdf_detail_module,
        "_recognise_image_bytes",
        lambda _data: "采购人：扫描件测试单位\n资格要求：依法注册",
    )

    pages, page_count, reason = pdf_detail_module._parse_pdf_bytes_sync(
        data, max_pages=10
    )

    assert reason is None
    assert page_count == 1
    assert pages[0]["method"] == "ocr"
    assert "扫描件测试单位" in pages[0]["text"]


@pytest.mark.asyncio
async def test_access_prompt_pdf_is_rejected_after_memory_capture(monkeypatch):
    monkeypatch.setattr(
        pdf_detail_module,
        "_parse_pdf_bytes_sync",
        lambda _data, *, max_pages: (
            [
                {
                    "page": 1,
                    "text": "页面访问提示：当前页面已暂停访问，且不再提供PDF文件的查看服务。",
                    "method": "pypdf_text",
                }
            ],
            1,
            None,
        ),
    )
    captured = PublicPdfDetail(
        status="captured",
        detail_url="https://ctbpsp.com/#/bulletinDetail?uuid=" + "f" * 32,
        document_page_count=1,
        document_bytes=b"%PDF-1.4 test-only",
    )

    result = await pdf_detail_module._finalise_captured_pdf(
        captured,
        expected_title="测试服务器采购项目招标公告",
        expected_project_code=None,
    )

    assert result.status == "metadata_only"
    assert result.failure_reason == "official_content_unavailable"
    assert result.pages == []
    assert result.document_bytes is None


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
async def test_operation_queue_limits_public_page_work_to_two(tmp_path, monkeypatch):
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
    page_ids: set[int] = set()

    async def worker(name: str):
        nonlocal active, maximum_active
        async with browser.acquire() as lease:
            assert lease.context is browser._browser.contexts[0]
            page_ids.add(id(lease.page))
            active += 1
            maximum_active = max(maximum_active, active)
            order.append(f"start-{name}")
            await asyncio.sleep(0.02)
            order.append(f"end-{name}")
            active -= 1

    await asyncio.gather(worker("a"), worker("b"), worker("c"))

    assert maximum_active == 2
    assert len(page_ids) == 2
    assert sorted(value for value in order if value.startswith("start-")) == [
        "start-a",
        "start-b",
        "start-c",
    ]
    assert browser.status()["state"] == "ready"


@pytest.mark.asyncio
async def test_interactive_lease_waits_for_workers_and_blocks_new_auto_work(
    tmp_path, monkeypatch
):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.runtime_file = tmp_path / "runtime.json"
    browser._browser = _FakeBrowser()
    browser._state = "ready"

    async def already_started():
        return True

    monkeypatch.setattr(browser, "ensure_started", already_started)
    monkeypatch.setattr(browser, "_bring_to_front", already_started)
    auto_ready = asyncio.Event()
    auto_release = asyncio.Event()
    interactive_started = asyncio.Event()
    interactive_release = asyncio.Event()
    late_auto_started = asyncio.Event()
    ready_count = 0

    async def auto_worker():
        nonlocal ready_count
        async with browser.acquire():
            ready_count += 1
            if ready_count == 2:
                auto_ready.set()
            await auto_release.wait()

    async def interactive_worker():
        async with browser.acquire(interactive=True):
            interactive_started.set()
            await interactive_release.wait()

    async def late_auto_worker():
        async with browser.acquire():
            late_auto_started.set()

    first = asyncio.create_task(auto_worker())
    second = asyncio.create_task(auto_worker())
    await auto_ready.wait()
    interactive = asyncio.create_task(interactive_worker())
    await asyncio.sleep(0.01)
    late = asyncio.create_task(late_auto_worker())
    await asyncio.sleep(0.01)
    assert interactive_started.is_set() is False
    assert late_auto_started.is_set() is False

    auto_release.set()
    await interactive_started.wait()
    assert late_auto_started.is_set() is False
    interactive_release.set()
    await asyncio.gather(first, second, interactive, late)
    assert late_auto_started.is_set() is True


@pytest.mark.asyncio
async def test_managed_browser_reuses_two_independent_work_pages(tmp_path):
    class Page:
        def is_closed(self):
            return False

    class Context:
        def __init__(self):
            self.pages = [Page()]
            self.created = 0

        async def new_page(self):
            self.created += 1
            return Page()

    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.runtime_file = tmp_path / "runtime.json"
    context = Context()

    first = await browser._checkout_page(context)
    second = await browser._checkout_page(context)
    await browser._return_page(first)
    third = await browser._checkout_page(context)

    assert first is context.pages[0]
    assert second is not first
    assert third is first
    assert context.created == 1


@pytest.mark.asyncio
async def test_managed_browser_replaces_only_failed_lease_page(tmp_path, monkeypatch):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.runtime_file = tmp_path / "runtime.json"
    browser._browser = _FakeBrowser()
    browser._state = "ready"

    async def already_started():
        return True

    monkeypatch.setattr(browser, "ensure_started", already_started)
    async with browser.acquire() as lease:
        original = lease.page
        replacement = await browser.replace_page(lease)
        assert original.closed is True
        assert replacement is lease.page
        assert replacement is not original
        assert browser.status()["active_workers"] == 1

    assert replacement in browser._available_pages
    assert original not in browser._available_pages
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
    browser._browser = _FakeBrowser()
    browser._state = "ready"

    status = browser.status()

    assert status["state"] == "ready"
    assert status["engine"] is None
    assert status["profile_ready"] is True
    assert status["last_error"] is None
    assert status["pool_size"] == 2
    assert status["active_workers"] == 0
    assert status["queue_size"] == 0
    assert status["adaptive_mode"] is False
    assert "44001" not in str(status)
    assert str(browser.profile_dir) not in str(status)


def test_public_status_reports_exited_browser_instead_of_stale_ready(tmp_path):
    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.runtime_file = tmp_path / "runtime.json"
    browser._browser = _FakeBrowser()
    browser._process = _FakeProcess(44501)
    browser._process.returncode = 0
    browser._state = "ready"

    status = browser.status()

    assert status["state"] == "unavailable"
    assert "自动重启" in status["last_error"]


@pytest.mark.asyncio
async def test_reconnect_disconnects_driver_without_closing_managed_browser(tmp_path):
    class FakePlaywright:
        stopped = False

        async def stop(self):
            self.stopped = True

    browser = ManagedPublicBrowser()
    browser.profile_dir = tmp_path / "profile"
    browser.runtime_file = tmp_path / "runtime.json"
    fake_browser = _FakeBrowser()
    fake_playwright = FakePlaywright()
    browser._browser = fake_browser
    browser._playwright = fake_playwright

    await browser._disconnect_playwright()

    assert fake_playwright.stopped is True
    assert fake_browser.closed is False


def test_public_error_redacts_local_path_and_loopback_port():
    message = managed_module._public_error_message(
        r"failed C:\Users\example\profile at 127.0.0.1:44001"
    )

    assert "C:\\Users" not in message
    assert "44001" not in message


@pytest.mark.asyncio
async def test_pdf_collection_restarts_managed_browser_once_after_crash(monkeypatch):
    class FakePage:
        def __init__(self, crash=False):
            self.crash = crash

    class FakeBroker:
        def __init__(self):
            self.acquire_count = 0
            self.shutdown_count = 0

        @asynccontextmanager
        async def acquire(self, *, interactive=False):
            self.acquire_count += 1
            attempt = self.acquire_count
            yield SimpleNamespace(
                context=object(),
                page=FakePage(crash=attempt == 1),
                reused=attempt > 1,
                engine="chrome",
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
        if _page.crash:
            raise RuntimeError("browser process exited")
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
    assert broker.shutdown_count == 0


@pytest.mark.asyncio
async def test_pdf_collection_timeout_releases_page_and_retries_once(monkeypatch):
    class FakeBroker:
        acquire_count = 0

        @asynccontextmanager
        async def acquire(self, *, interactive=False):
            self.acquire_count += 1
            yield SimpleNamespace(
                context=object(), page=object(), reused=False, engine="chrome"
            )

        @property
        def is_connected(self):
            return True

        def status(self):
            return {"state": "ready"}

        def mark_needs_verification(self):
            return None

    broker = FakeBroker()
    monkeypatch.setattr(managed_module, "get_managed_public_browser", lambda: broker)

    async def collect(_page, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(pdf_detail_module, "_collect_public_pdf_detail", collect)
    result = await pdf_detail_module._fetch_managed_public_pdf_detail(
        detail_url="https://ctbpsp.com/#/bulletinDetail?uuid=" + "b" * 32,
        expected_id="b" * 32,
        expected_title="扫描公告",
        timeout_ms=60_000,
        headless=True,
    )

    assert result.failure_reason == "collector_timeout"
    assert result.failure_stage == "pdf_frame"
    assert result.attempt_count == 2
    assert broker.acquire_count == 2


@pytest.mark.asyncio
async def test_identity_failure_rebuilds_only_current_page_then_succeeds(monkeypatch):
    class Page:
        pass

    class FakeBroker:
        acquire_count = 0
        replace_count = 0

        @asynccontextmanager
        async def acquire(self, *, interactive=False):
            self.acquire_count += 1
            yield SimpleNamespace(
                context=object(), page=Page(), reused=True, engine="chrome"
            )

        async def replace_page(self, lease):
            self.replace_count += 1
            lease.page = Page()
            return lease.page

        @property
        def is_connected(self):
            return True

        def status(self):
            return {"state": "ready"}

        def mark_needs_verification(self):
            return None

    broker = FakeBroker()
    monkeypatch.setattr(managed_module, "get_managed_public_browser", lambda: broker)
    calls = 0

    async def collect(_page, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return PublicPdfDetail(
                status="failed",
                detail_url=kwargs["detail_url"],
                failure_reason="identity_mismatch",
            )
        return PublicPdfDetail(
            status="full",
            detail_url=kwargs["detail_url"],
            content_format="pdf_text",
            clean_content="【第1页】\n招标人：测试单位",
            pages=[{"page": 1, "text": "招标人：测试单位"}],
        )

    monkeypatch.setattr(pdf_detail_module, "_collect_public_pdf_detail", collect)
    result = await pdf_detail_module._fetch_managed_public_pdf_detail(
        detail_url="https://ctbpsp.com/#/bulletinDetail?uuid=" + "c" * 32,
        expected_id="c" * 32,
        expected_title="测试公告",
        timeout_ms=60_000,
        headless=True,
    )

    assert result.status == "full"
    assert result.attempt_count == 2
    assert broker.acquire_count == 2
    assert broker.replace_count == 1


@pytest.mark.asyncio
async def test_pdf_bytes_are_parsed_after_browser_lease_is_released(monkeypatch):
    class FakeBroker:
        active = False

        @asynccontextmanager
        async def acquire(self, *, interactive=False):
            self.active = True
            try:
                yield SimpleNamespace(
                    context=object(), page=object(), reused=True, engine="chrome"
                )
            finally:
                self.active = False

        @property
        def is_connected(self):
            return True

        def status(self):
            return {"state": "ready"}

        def mark_needs_verification(self):
            return None

    broker = FakeBroker()
    monkeypatch.setattr(managed_module, "get_managed_public_browser", lambda: broker)

    async def collect(_page, **kwargs):
        return PublicPdfDetail(
            status="captured",
            detail_url=kwargs["detail_url"],
            document_page_count=1,
            document_bytes=b"%PDF-1.4 test-only",
        )

    def parse(_data, *, max_pages):
        assert broker.active is False
        assert max_pages >= 1
        return (
            [{"page": 1, "text": "测试服务器采购项目 招标人：测试单位", "method": "pypdf_text"}],
            1,
            None,
        )

    monkeypatch.setattr(pdf_detail_module, "_collect_public_pdf_detail", collect)
    monkeypatch.setattr(pdf_detail_module, "_parse_pdf_bytes_sync", parse)
    result = await pdf_detail_module._fetch_managed_public_pdf_detail(
        detail_url="https://ctbpsp.com/#/bulletinDetail?uuid=" + "e" * 32,
        expected_id="e" * 32,
        expected_title="测试服务器采购项目招标公告",
        timeout_ms=60_000,
        headless=True,
    )

    assert result.status == "full"
    assert result.acquisition_path == "pdfjs_memory_bytes+local_parser"
    assert result.document_bytes is None
    assert result.document_page_count == 1
