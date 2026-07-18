# FusionBid 官方公告采集扩展

用于自动化浏览器被官方站点限制、但用户常用 Chrome 可以正常显示公告时，
从当前 PDF.js 阅读器逐页提取文字层并发送到本机 FusionBid。

## 安装

1. 在 Chrome 打开 `chrome://extensions/`。
2. 开启右上角“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择本目录：`browser_extension/ctbpsp_capture`。

## 使用

1. 确认 FusionBid 正在 `http://127.0.0.1:8000` 运行，且公告已经存在于采集结果中。
2. 使用常用 Chrome 打开对应的 `ctbpsp.com/#/bulletinDetail?...` 官方详情页。
3. 等待内嵌 PDF 阅读器显示正文。
4. 点击 Chrome 工具栏中的“FusionBid 官方公告采集”，再点击“采集当前 PDF 公告”。
5. 成功后返回 FusionBid 刷新详情。

扩展只读取公告外层标题、UUID、逐页文字和文字坐标，并发送到本机
`127.0.0.1`。它不会读取或上传 Cookie、密码、storage state，也不会绕过验证码。
