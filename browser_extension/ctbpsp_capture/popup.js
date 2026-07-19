const captureButton = document.querySelector("#capture");
const statusBox = document.querySelector("#status");

function setStatus(message, type = "") {
  statusBox.textContent = message;
  statusBox.className = type;
}

async function collectFrame() {
  const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const pages = Array.from(document.querySelectorAll(".page"));
  if (!pages.length) {
    return {
      kind: "outer",
      url: window.location.href,
      text: (document.body?.innerText || "").slice(0, 200000),
    };
  }

  const viewer = document.querySelector("#viewerContainer");
  const originalScrollTop = viewer?.scrollTop || 0;
  const extractedPages = [];
  try {
    for (let index = 0; index < Math.min(pages.length, 100); index += 1) {
      const page = pages[index];
      page.scrollIntoView({ block: "center", behavior: "auto" });
      let layer = null;
      for (let attempt = 0; attempt < 24; attempt += 1) {
        layer = page.querySelector(".textLayer");
        if (layer && layer.querySelectorAll("span").length > 0) break;
        await wait(250);
      }
      if (!layer) {
        extractedPages.push({ page: index + 1, text: "", items: [] });
        continue;
      }
      const layerRect = layer.getBoundingClientRect();
      const items = Array.from(layer.querySelectorAll("span")).map((span) => {
        const rect = span.getBoundingClientRect();
        return {
          text: span.textContent || "",
          x: rect.left - layerRect.left,
          y: layerRect.bottom - rect.bottom,
          width: rect.width,
          height: rect.height,
        };
      });
      extractedPages.push({
        page: Number(page.dataset.pageNumber) || index + 1,
        text: layer.innerText || layer.textContent || "",
        items,
      });
    }
  } finally {
    if (viewer) viewer.scrollTop = originalScrollTop;
  }
  return {
    kind: "pdf",
    url: window.location.href,
    pageCount: pages.length,
    pages: extractedPages,
  };
}

captureButton.addEventListener("click", async () => {
  captureButton.disabled = true;
  setStatus("正在逐页读取 PDF.js 文字层，请保持此窗口打开……");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id || !tab.url?.startsWith("https://ctbpsp.com/#/bulletinDetail")) {
      throw new Error("当前标签页不是 ctbpsp.com 官方公告详情页");
    }
    const uuid = new URL(tab.url).hash.match(/[?&]uuid=([0-9a-fA-F]{32})(?:&|$)/)?.[1];
    if (!uuid) throw new Error("当前官方页缺少有效 UUID");

    const frameResults = await chrome.scripting.executeScript({
      target: { tabId: tab.id, allFrames: true },
      func: collectFrame,
    });
    const outer = frameResults.find((row) => row.frameId === 0)?.result;
    const pdf = frameResults.map((row) => row.result).find((row) => row?.kind === "pdf");
    if (!outer?.text) throw new Error("未读取到官方详情页标题");
    if (!pdf?.pages?.length) throw new Error("未检测到已经加载的 PDF.js 文字层");
    if (pdf.pages.some((page) => !page.items?.length && !page.text?.trim())) {
      throw new Error("存在尚未加载的 PDF 页面，请滚动浏览一次后重试");
    }

    setStatus(`已读取 ${pdf.pages.length} 页，正在发送到本机 FusionBid……`);
    const response = await fetch(
      "http://127.0.0.1:8000/api/announcements/capture-rendered-detail",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_name: "cebpub",
          source_item_id: uuid,
          detail_url: tab.url,
          outer_text: outer.text,
          page_count: pdf.pageCount,
          pages: pdf.pages,
        }),
      },
    );
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || `FusionBid 返回 ${response.status}`);
    const purchaser = payload.announcement?.fields?.purchaser || "待查看";
    setStatus(
      `采集成功：${payload.page_count} 页\n采购主体：${purchaser}\n请返回 FusionBid 刷新详情。`,
      "success",
    );
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "采集失败", "error");
  } finally {
    captureButton.disabled = false;
  }
});
