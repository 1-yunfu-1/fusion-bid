# 意图解析大模型：API + 本地 Ollama 双模式

> FusionBid 智标聚合助手  
> 优先级：**兼容 API → 本地 Ollama → 规则降级**  
> 密钥仅环境变量，不进 Git / 不进前端。

---

## 1. 设计原则

| 原则 | 说明 |
|------|------|
| API 优先 | 有 `LLM_API_KEY` 且通道启用时，先走 OpenAI 兼容 Chat Completions |
| Ollama 次之 | API 失败/超时/未配置时，尝试本机 Ollama |
| 规则兜底 | 两者皆不可用时，使用规则解析，保证系统可演示 |
| 人工确认 | 无论哪条通道，结果均可在前端修改后再创建任务 |
| 可自选模型 | 运行时可改 API 模型名、Ollama 模型名；可拉取 Ollama 模型 |
| 可自建接入 | 任意 OpenAI 兼容网关：改 `Base URL` + `Model` + Key |

解析链路：

```text
用户自然语言
  → [1] 兼容 API（LLM_BASE_URL + LLM_API_KEY + 模型）
  → [2] Ollama（OLLAMA_BASE_URL + 本地模型）
  → [3] 规则解析（区域/时间/频率/关键词）
  → 严格校验
  → 人工确认/修改 → 保存 SearchTask
```

---

## 2. 环境变量

复制 `.env.example` 为 `.env`：

```bash
# 总开关（为 true 时才会尝试大模型；false 则纯规则）
LLM_ENABLED=true

# 通道顺序（也可在设置页/runtime 修改）
LLM_PREFER_ORDER=api,ollama,rule

# ---------- 优先：兼容 API ----------
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-key-here
LLM_MODEL=gpt-4o-mini
LLM_TIMEOUT=60

# ---------- 其次：本地 Ollama ----------
OLLAMA_ENABLED=true
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:3b
OLLAMA_TIMEOUT=120
```

### 2.1 常见 API 接入示例

| 服务 | LLM_BASE_URL 示例 | 模型名示例 |
|------|-------------------|------------|
| OpenAI | `https://api.openai.com/v1` | `gpt-4o-mini` |
| DeepSeek | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义（兼容模式以厂商文档为准） | 厂商兼容 endpoint | 对应 model id |
| 自建 vLLM / OneAPI / NewAPI | `http://host:port/v1` | 你部署的模型名 |

**只改这三项即可接入自有模型服务：Base URL、API Key、Model。**

### 2.2 安全与 Key 填写接口

- Key 可放在：
  1. **设置页 / API** 保存到 `data/llm_secrets.json`（已 gitignore，**推荐本机演示**）
  2. 环境变量 `LLM_API_KEY` / `.env`（同样禁止提交 Git）
- **禁止**写入代码、禁止写入 `data/llm_runtime.json`、禁止提交 Git  
- 对外接口**永不返回完整 Key**，仅返回 `configured` + 脱敏 `hint`（如 `sk-Q...Rb1Q5`）  
- 日志脱敏 `Bearer ***`

```http
PUT /api/llm/credentials
Content-Type: application/json

{"api_key":"sk-your-key"}

# 清除
{"clear": true}

# 查询状态（无明文）
GET /api/llm/credentials
```

---

## 3. 本地 Ollama

### 3.1 安装

1. 打开 [https://ollama.com](https://ollama.com) 下载并安装  
2. 确认服务：浏览器或命令行访问 `http://127.0.0.1:11434`  
3. 命令行检查：

```bash
ollama --version
ollama list
```

### 3.2 下载 / 选择模型

**方式 A — 命令行（推荐首次）**

```bash
# 小参数中文友好（默认推荐）
ollama pull qwen2.5:3b

# 效果更好（需更多内存）
ollama pull qwen2.5:7b

# 其他可选
ollama pull llama3.2:3b
ollama pull phi3:mini
```

然后：

```bash
# .env
OLLAMA_MODEL=qwen2.5:3b
```

或在前端 **设置 → 已安装模型 → 选用**。

**方式 B — 本系统 UI / API**

- 页面：`设置` → 「Ollama 本地模型：选择 / 下载 / 自定义」  
- 填入模型名 → **拉取/下载**（调用 Ollama `/api/pull`，可能较久）  
- 或 **仅设为当前模型**（用于已安装或即将安装的名称）

**方式 C — HTTP API**

```http
GET  /api/llm/status
GET  /api/llm/ollama/models
POST /api/llm/ollama/pull   {"model":"qwen2.5:3b"}
POST /api/llm/ollama/select {"model":"qwen2.5:3b"}
PUT  /api/llm/runtime       {"ollama_model":"qwen2.5:3b","prefer_order":["api","ollama","rule"]}
```

### 3.3 自定义已有本地模型

若你已通过其他方式导入 GGUF 到 Ollama，或使用了自定义 tag：

1. `ollama list` 确认名称  
2. 在设置页「当前 Ollama 模型」填写完整名称（如 `myorg/intent:latest`）  
3. 保存运行时配置  

Ollama 对外使用 **OpenAI 兼容** 接口：`{OLLAMA_BASE_URL}/v1/chat/completions`。

### 3.4 内存建议（意图槽位任务）

| 模型体量 | 内存粗算 | 适用 |
|----------|----------|------|
| 1.5B～3B | 约 4～8GB | 意图解析足够 |
| 7B | 约 8～16GB | 更稳，稍慢 |
| 14B+ | 更高 | 一般不必为意图解析单独上 |

---

## 4. 运行时配置文件

用户在设置页保存的选项写入（**无 Key**）：

```text
data/llm_runtime.json
```

示例：

```json
{
  "prefer_order": ["api", "ollama", "rule"],
  "api_model": "gpt-4o-mini",
  "api_base_url": "https://api.openai.com/v1",
  "ollama_model": "qwen2.5:3b",
  "ollama_base_url": "http://127.0.0.1:11434",
  "api_enabled": true,
  "ollama_enabled": true
}
```

优先级：**运行时覆盖环境变量中的模型名/URL**；Key 永远只读环境变量。

该文件已在 `.gitignore` 数据策略下位于 `data/`，勿提交含敏感信息的自定义拷贝。

---

## 5. API 模型探测与选择

兼容 OpenAI 的服务一般提供：

```http
GET {LLM_BASE_URL}/models
Authorization: Bearer {LLM_API_KEY}
```

本系统封装：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/llm/models` | 用当前配置探测可用模型列表 |
| POST | `/api/llm/models/probe` | 可临时指定 `base_url` 探测 |
| POST | `/api/llm/models/select` | 选择模型写入 `data/llm_runtime.json` |

前端 **设置** 页：

1. 填写 Base URL，确保 `.env` 中有 `LLM_API_KEY`  
2. 点击 **探测 API 可用模型**  
3. 在列表中 **选用**，或手动输入自定义模型名后「设为当前 API 模型」  

探测响应**不包含** API Key。

## 6. 前端入口

| 页面 | 作用 |
|------|------|
| **新建检索** | 输入自然语言 → 解析 → 确认/改字段 → 创建任务 |
| **设置** | 探测/选择 API 模型；Ollama 列表/拉取；改优先级 |
| **任务列表** | 查看已确认意图任务（采集待后续阶段） |

---

## 7. 离线 / 无 Key 答辩

```bash
LLM_ENABLED=true
# 不填 LLM_API_KEY
OLLAMA_ENABLED=true
OLLAMA_MODEL=qwen2.5:3b
```

或完全离线：

```bash
LLM_ENABLED=false
# 或 OLLAMA_ENABLED=false 且无 Key
```

此时自动 **规则解析 + 人工确认**，五个验收句仍应可改字段通过。

---

## 8. 故障排查

| 现象 | 处理 |
|------|------|
| API health 失败 | 检查网络、Base URL 是否带 `/v1`、Key 是否有效 |
| Ollama 连不上 | 确认进程已启动；防火墙；`OLLAMA_BASE_URL` |
| 拉取超时 | 加大 `OLLAMA_TIMEOUT`；命令行先 `ollama pull` |
| 解析乱字段 | 换更大一点模型；或在确认页手工改；规则会 hybrid 补空 |
| 过期「今天 9:00」 | 系统报错提示，须改立即执行或改有效时间，禁止静默建过期任务 |

---

## 9. 相关代码

| 路径 | 说明 |
|------|------|
| `backend/app/llm/client.py` | API/Ollama 调用、列表、拉取 |
| `backend/app/core/llm_runtime.py` | 运行时模型选择 |
| `backend/app/parsers/service.py` | 编排与降级 |
| `backend/app/api/llm.py` | `/api/llm/*` |
| `backend/app/api/parse.py` | `/api/parse` |
| `frontend/src/pages/SettingsPage.tsx` | 双模式 UI |
| `frontend/src/pages/NewTaskPage.tsx` | 解析确认 UI |

---

## 10. 与赛题关系

- ✅ 大模型结构化解析（API 或 Ollama）  
- ✅ 规则降级  
- ✅ 严格校验  
- ✅ 人工确认修改  
- ✅ Key 不入库  
- ✅ 不硬编码五个案例答案  
