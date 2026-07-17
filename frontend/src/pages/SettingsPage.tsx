import { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Form,
  Input,
  List,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activateApiProfile,
  createApiProfile,
  deleteApiProfile,
  fetchApiModels,
  fetchApiProfiles,
  fetchLlmStatus,
  fetchOllamaModels,
  probeApiModels,
  pullOllamaModel,
  selectApiModel,
  selectOllamaModel,
  updateApiProfile,
  updateLlmRuntime,
  type ApiModelsResponse,
  type ApiProfile,
} from "../api/llm";

type ProfileForm = {
  name: string;
  base_url: string;
  api_key: string;
  model: string;
  activate: boolean;
};

const emptyProfileForm = (): ProfileForm => ({
  name: "",
  base_url: "https://api.openai.com/v1",
  api_key: "",
  model: "",
  activate: true,
});

export default function SettingsPage() {
  const qc = useQueryClient();
  const [pullName, setPullName] = useState("qwen2.5:3b");
  const [apiModels, setApiModels] = useState<ApiModelsResponse | null>(null);
  const [customApiModel, setCustomApiModel] = useState("");
  const [form] = Form.useForm();
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [editing, setEditing] = useState<ApiProfile | null>(null);
  const [profileForm, setProfileForm] = useState<ProfileForm>(emptyProfileForm());

  const statusQuery = useQuery({ queryKey: ["llm-status"], queryFn: fetchLlmStatus });
  const profilesQuery = useQuery({ queryKey: ["llm-profiles"], queryFn: fetchApiProfiles });
  const modelsQuery = useQuery({
    queryKey: ["ollama-models"],
    queryFn: fetchOllamaModels,
    retry: false,
  });

  useEffect(() => {
    if (statusQuery.data) {
      const r = statusQuery.data.runtime.current as Record<string, unknown>;
      form.setFieldsValue({
        prefer_order: statusQuery.data.prefer_order,
        api_model: statusQuery.data.api.model,
        api_base_url: statusQuery.data.api.base_url,
        api_enabled: statusQuery.data.api.enabled,
        ollama_model: statusQuery.data.ollama.model,
        ollama_base_url: statusQuery.data.ollama.base_url,
        ollama_enabled: statusQuery.data.ollama.enabled,
        ...r,
      });
      setCustomApiModel(statusQuery.data.api.model || "");
    }
  }, [statusQuery.data, form]);

  const invalidateLlm = () => {
    qc.invalidateQueries({ queryKey: ["llm-status"] });
    qc.invalidateQueries({ queryKey: ["llm-profiles"] });
  };

  const saveMutation = useMutation({
    mutationFn: (values: Record<string, unknown>) => updateLlmRuntime(values),
    onSuccess: () => {
      message.success("运行时配置已保存");
      invalidateLlm();
    },
    onError: () => message.error("保存失败"),
  });

  const saveProfileMutation = useMutation({
    mutationFn: async () => {
      const payload = {
        name: profileForm.name.trim(),
        base_url: profileForm.base_url.trim(),
        model: profileForm.model.trim() || null,
        activate: profileForm.activate,
        api_key: profileForm.api_key.trim() || undefined,
      };
      if (!payload.name) throw new Error("请填写配置名称");
      if (editing) {
        return updateApiProfile(editing.id, payload);
      }
      if (!payload.api_key) throw new Error("新建配置时必须填写 API Key");
      return createApiProfile(payload);
    },
    onSuccess: (data) => {
      message.success(data.message || "配置已保存");
      setProfileModalOpen(false);
      setEditing(null);
      setProfileForm(emptyProfileForm());
      invalidateLlm();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      message.error(
        typeof err.response?.data?.detail === "string"
          ? err.response.data.detail
          : err.message || "保存失败",
      );
    },
  });

  const activateMutation = useMutation({
    mutationFn: (id: string) => activateApiProfile(id),
    onSuccess: (data) => {
      message.success(data.message || "已切换配置");
      invalidateLlm();
    },
    onError: () => message.error("切换失败"),
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteApiProfile(id),
    onSuccess: (data) => {
      message.success(data.message || "已删除");
      invalidateLlm();
    },
    onError: () => message.error("删除失败"),
  });

  const probeApiMutation = useMutation({
    mutationFn: async () => {
      const base = (form.getFieldValue("api_base_url") as string | undefined)?.trim();
      if (base) return probeApiModels(base);
      return fetchApiModels();
    },
    onSuccess: (data) => {
      setApiModels(data);
      if (data.ok) message.success(data.message || `探测到 ${data.count ?? 0} 个模型`);
      else message.warning(data.message || "探测失败");
    },
    onError: (e: Error) => message.error(e.message || "探测失败"),
  });

  const selectApiMutation = useMutation({
    mutationFn: (name: string) => selectApiModel(name),
    onSuccess: (data) => {
      message.success(data.message || `已选择：${data.api_model}`);
      form.setFieldValue("api_model", data.api_model);
      setCustomApiModel(data.api_model);
      invalidateLlm();
    },
    onError: () => message.error("选择 API 模型失败"),
  });

  const pullMutation = useMutation({
    mutationFn: () => pullOllamaModel(pullName.trim()),
    onSuccess: (data) => {
      if (data.ok) {
        message.success(`模型已就绪：${data.model}`);
        qc.invalidateQueries({ queryKey: ["ollama-models"] });
        invalidateLlm();
      } else {
        message.error(String(data.status || "拉取失败"));
      }
    },
    onError: (e: Error) => message.error(e.message || "拉取失败（请确认 Ollama 已启动）"),
  });

  const selectMutation = useMutation({
    mutationFn: (name: string) => selectOllamaModel(name),
    onSuccess: () => {
      message.success("已选择 Ollama 模型");
      invalidateLlm();
    },
  });

  const status = statusQuery.data;
  const profiles: ApiProfile[] = profilesQuery.data?.profiles || [];

  const apiModelOptions = useMemo(
    () =>
      (apiModels?.models || []).map((m) => ({
        value: m.id,
        label: m.owned_by ? `${m.id}（${m.owned_by}）` : m.id,
      })),
    [apiModels],
  );

  const openCreate = () => {
    setEditing(null);
    setProfileForm({
      ...emptyProfileForm(),
      base_url: status?.api.base_url || "https://api.openai.com/v1",
    });
    setProfileModalOpen(true);
  };

  const openEdit = (p: ApiProfile) => {
    setEditing(p);
    setProfileForm({
      name: p.name,
      base_url: p.base_url || "",
      api_key: "",
      model: p.model || "",
      activate: !!p.is_active,
    });
    setProfileModalOpen(true);
  };

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="大模型双模式：优先 API，其次 Ollama，最后规则"
        description={
          <div>
            <p style={{ marginBottom: 4 }}>
              可在下方保存<strong>多组 API 配置</strong>（名称、Base URL、API Key、默认模型），随时切换选用。
            </p>
            <p style={{ margin: 0 }}>
              Key 仅存本地 <code>data/llm_secrets.json</code>（已 gitignore），接口永不回显明文；也可兜底使用环境变量{" "}
              <code>LLM_API_KEY</code>。
            </p>
          </div>
        }
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card title="通道状态" loading={statusQuery.isLoading} className="page-card">
            {status && (
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label="优先级">
                  {status.prefer_order.map((p) => (
                    <Tag key={p}>{p}</Tag>
                  ))}
                </Descriptions.Item>
                <Descriptions.Item label="API">
                  <Tag color={status.api.health?.ok ? "success" : "default"}>
                    {status.api.enabled ? "启用" : "关闭"}
                  </Tag>
                  {status.api.key_configured ? (
                    <Tag color="blue">
                      Key 已配置
                      {status.api.key_source ? `（${status.api.key_source}）` : ""}
                    </Tag>
                  ) : (
                    <Tag color="orange">Key 未配置</Tag>
                  )}
                  {status.api.active_profile_name && (
                    <Tag color="purple">当前组: {status.api.active_profile_name}</Tag>
                  )}
                  {status.api.key_hint && (
                    <div className="muted">Key 提示: {status.api.key_hint}</div>
                  )}
                  <div className="muted">{status.api.base_url}</div>
                  <div>当前模型: {status.api.model}</div>
                  <div className="muted">{status.api.health?.message}</div>
                </Descriptions.Item>
                <Descriptions.Item label="Ollama">
                  <Tag color={status.ollama.health?.ok ? "success" : "default"}>
                    {status.ollama.enabled ? "启用" : "关闭"}
                  </Tag>
                  <div className="muted">{status.ollama.base_url}</div>
                  <div>模型: {status.ollama.model}</div>
                  <div className="muted">{status.ollama.health?.message}</div>
                </Descriptions.Item>
              </Descriptions>
            )}
            {status?.notes.map((n) => (
              <Typography.Paragraph key={n} type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
                · {n}
              </Typography.Paragraph>
            ))}
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card title="运行时配置" className="page-card">
            <Form form={form} layout="vertical" onFinish={(v) => saveMutation.mutate(v)}>
              <Form.Item name="prefer_order" label="通道优先级">
                <Select
                  mode="multiple"
                  options={[
                    { value: "api", label: "api（兼容云端/自建）" },
                    { value: "ollama", label: "ollama（本地）" },
                    { value: "rule", label: "rule（规则降级）" },
                  ]}
                />
              </Form.Item>
              <Form.Item name="api_enabled" label="启用 API" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item
                name="api_base_url"
                label="API Base URL（当前生效，切换配置组会同步）"
              >
                <Input placeholder="https://api.openai.com/v1 或兼容网关" />
              </Form.Item>
              <Form.Item name="api_model" label="API 模型名（可探测后选择）">
                <Input placeholder="gpt-4o-mini / deepseek-chat / ..." />
              </Form.Item>
              <Form.Item name="ollama_enabled" label="启用 Ollama" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="ollama_base_url" label="Ollama 地址">
                <Input placeholder="http://127.0.0.1:11434" />
              </Form.Item>
              <Form.Item name="ollama_model" label="当前 Ollama 模型">
                <Input placeholder="qwen2.5:3b" />
              </Form.Item>
              <Button type="primary" htmlType="submit" loading={saveMutation.isPending}>
                保存运行时配置
              </Button>
            </Form>
          </Card>
        </Col>
      </Row>

      <Card
        title="API 配置组（多路径 + Key）"
        className="page-card"
        loading={profilesQuery.isLoading}
        extra={
          <Button type="primary" onClick={openCreate}>
            新增配置
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="可保存多组供应商 / 网关"
          description="例如 OpenAI、DeepSeek、自建网关各存一组；点「设为当前」切换。密钥脱敏显示，编辑时留空 Key 表示不修改原密钥。"
        />

        {status?.api.key_configured ? (
          <Alert
            type="success"
            showIcon
            style={{ marginBottom: 12 }}
            message={status.api.key_message || "已配置 API Key"}
            description={
              status.api.key_hint
                ? `脱敏：${status.api.key_hint} · 配置数：${status.api.profile_count ?? profiles.length}`
                : `配置数：${status.api.profile_count ?? profiles.length}`
            }
          />
        ) : (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 12 }}
            message="尚未配置可用 API Key"
            description="请点击右上角「新增配置」填写名称、Base URL 与 Key。"
          />
        )}

        <Table
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={profiles}
          locale={{ emptyText: "暂无配置，请新增" }}
          columns={[
            {
              title: "名称",
              dataIndex: "name",
              render: (v: string, r) => (
                <Space>
                  <strong>{v}</strong>
                  {r.is_active && <Tag color="blue">当前</Tag>}
                </Space>
              ),
            },
            {
              title: "Base URL",
              dataIndex: "base_url",
              ellipsis: true,
              render: (v: string) => v || <span className="muted">（用环境默认）</span>,
            },
            {
              title: "默认模型",
              dataIndex: "model",
              render: (v: string) => v || "—",
            },
            {
              title: "Key",
              key: "key",
              width: 140,
              render: (_, r) =>
                r.key_configured ? (
                  <Tag color="success">{r.key_hint || "已配置"}</Tag>
                ) : (
                  <Tag color="orange">无</Tag>
                ),
            },
            {
              title: "操作",
              key: "act",
              width: 220,
              render: (_, r) => (
                <Space wrap size={0}>
                  <Button
                    type="link"
                    size="small"
                    disabled={!!r.is_active}
                    loading={activateMutation.isPending}
                    onClick={() => activateMutation.mutate(r.id)}
                  >
                    设为当前
                  </Button>
                  <Button type="link" size="small" onClick={() => openEdit(r)}>
                    编辑
                  </Button>
                  <Popconfirm
                    title="确定删除该配置？"
                    onConfirm={() => deleteMutation.mutate(r.id)}
                  >
                    <Button type="link" size="small" danger loading={deleteMutation.isPending}>
                      删除
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title={editing ? `编辑配置：${editing.name}` : "新增 API 配置"}
        open={profileModalOpen}
        onCancel={() => {
          setProfileModalOpen(false);
          setEditing(null);
        }}
        onOk={() => saveProfileMutation.mutate()}
        confirmLoading={saveProfileMutation.isPending}
        okText="保存"
        destroyOnClose
        width={560}
      >
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <div>
            <Typography.Text type="secondary">名称</Typography.Text>
            <Input
              value={profileForm.name}
              onChange={(e) => setProfileForm((f) => ({ ...f, name: e.target.value }))}
              placeholder="如 OpenAI / DeepSeek / 公司网关"
            />
          </div>
          <div>
            <Typography.Text type="secondary">API Base URL</Typography.Text>
            <Input
              value={profileForm.base_url}
              onChange={(e) => setProfileForm((f) => ({ ...f, base_url: e.target.value }))}
              placeholder="https://api.openai.com/v1"
            />
          </div>
          <div>
            <Typography.Text type="secondary">
              API Key{editing ? "（留空则不修改原密钥）" : ""}
            </Typography.Text>
            <Input.Password
              value={profileForm.api_key}
              onChange={(e) => setProfileForm((f) => ({ ...f, api_key: e.target.value }))}
              placeholder={editing ? "•••• 留空保持原 Key" : "sk-... 或 Bearer Token"}
              autoComplete="off"
            />
          </div>
          <div>
            <Typography.Text type="secondary">默认模型（可选）</Typography.Text>
            <Input
              value={profileForm.model}
              onChange={(e) => setProfileForm((f) => ({ ...f, model: e.target.value }))}
              placeholder="gpt-4o-mini / deepseek-chat"
            />
          </div>
          <div>
            <Switch
              checked={profileForm.activate}
              onChange={(v) => setProfileForm((f) => ({ ...f, activate: v }))}
            />{" "}
            <Typography.Text>保存后设为当前配置</Typography.Text>
          </div>
        </Space>
      </Modal>

      <Card
        title="兼容 API 模型：探测 / 选择"
        className="page-card"
        extra={
          <Button
            type="primary"
            loading={probeApiMutation.isPending}
            onClick={() => probeApiMutation.mutate()}
          >
            探测 API 可用模型
          </Button>
        }
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="调用 OpenAI 兼容接口 GET {BaseURL}/models"
          description="使用当前激活配置组的 Key 与 Base URL。探测结果仅展示模型 id。"
        />
        {!status?.api.key_configured && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="未配置 API Key"
            description="请先在上方「API 配置组」中新增并设为当前，再点击探测。"
          />
        )}
        {apiModels && (
          <Alert
            type={apiModels.ok ? "success" : "error"}
            showIcon
            style={{ marginBottom: 16 }}
            message={apiModels.message}
            description={
              apiModels.base_url
                ? `端点：${apiModels.base_url}/models · 当前选用：${apiModels.selected || "—"}`
                : undefined
            }
          />
        )}

        <Typography.Title level={5}>探测到的模型</Typography.Title>
        {apiModelOptions.length > 0 ? (
          <Space direction="vertical" style={{ width: "100%" }} size="middle">
            <Select
              showSearch
              style={{ width: "100%" }}
              placeholder="从探测列表中选择模型"
              options={apiModelOptions}
              value={
                apiModelOptions.some((o) => o.value === (status?.api.model || customApiModel))
                  ? status?.api.model || customApiModel
                  : undefined
              }
              onChange={(v) => {
                setCustomApiModel(v);
                selectApiMutation.mutate(v);
              }}
              filterOption={(input, option) =>
                String(option?.label ?? option?.value ?? "")
                  .toLowerCase()
                  .includes(input.toLowerCase())
              }
            />
            <List
              size="small"
              bordered
              dataSource={apiModels?.models || []}
              style={{ maxHeight: 280, overflow: "auto" }}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Button
                      key="use"
                      type="link"
                      loading={selectApiMutation.isPending}
                      onClick={() => selectApiMutation.mutate(item.id)}
                    >
                      选用
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space>
                        <code>{item.id}</code>
                        {(status?.api.model === item.id || apiModels?.selected === item.id) && (
                          <Tag color="blue">当前</Tag>
                        )}
                      </Space>
                    }
                    description={item.owned_by ? `owned_by: ${item.owned_by}` : undefined}
                  />
                </List.Item>
              )}
            />
          </Space>
        ) : (
          <Typography.Paragraph type="secondary">
            尚未探测到模型列表。请配置 Key 与 Base URL 后点击右上角「探测 API 可用模型」。
          </Typography.Paragraph>
        )}

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          手动指定 / 自定义模型名
        </Typography.Title>
        <Space.Compact style={{ width: "100%" }}>
          <Input
            value={customApiModel}
            onChange={(e) => setCustomApiModel(e.target.value)}
            placeholder="如 gpt-4o-mini、deepseek-chat、qwen-plus"
          />
          <Button
            type="primary"
            loading={selectApiMutation.isPending}
            onClick={() => {
              const name = customApiModel.trim();
              if (!name) {
                message.warning("请输入模型名");
                return;
              }
              selectApiMutation.mutate(name);
            }}
          >
            设为当前 API 模型
          </Button>
        </Space.Compact>
      </Card>

      <Card title="Ollama 本地模型：选择 / 下载 / 自定义" className="page-card">
        {!modelsQuery.data?.ok && (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 16 }}
            message="未检测到 Ollama"
            description={
              modelsQuery.data?.message ||
              "请安装并启动 Ollama 后刷新。Windows 可访问 https://ollama.com 安装。"
            }
          />
        )}

        <Typography.Title level={5}>已安装模型</Typography.Title>
        <List
          loading={modelsQuery.isLoading}
          dataSource={modelsQuery.data?.models || []}
          locale={{ emptyText: "暂无本地模型" }}
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button
                  key="sel"
                  type="link"
                  onClick={() => selectMutation.mutate(item.name)}
                  loading={selectMutation.isPending}
                >
                  选用
                </Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    {item.name}
                    {modelsQuery.data?.selected === item.name && <Tag color="blue">当前</Tag>}
                  </Space>
                }
                description={
                  item.size ? `约 ${(item.size / 1024 / 1024 / 1024).toFixed(2)} GB` : undefined
                }
              />
            </List.Item>
          )}
        />

        <Typography.Title level={5} style={{ marginTop: 24 }}>
          推荐拉取
        </Typography.Title>
        <List
          size="small"
          dataSource={
            modelsQuery.data?.recommended || [
              { name: "qwen2.5:3b", note: "体积小，中文意图足够" },
              { name: "qwen2.5:7b", note: "效果更好" },
              { name: "llama3.2:3b", note: "通用小模型" },
            ]
          }
          renderItem={(item) => (
            <List.Item
              actions={[
                <Button key="p" type="link" onClick={() => setPullName(item.name)}>
                  填入
                </Button>,
              ]}
            >
              <code>{item.name}</code> — {item.note}
            </List.Item>
          )}
        />

        <Space.Compact style={{ width: "100%", marginTop: 16 }}>
          <Input
            value={pullName}
            onChange={(e) => setPullName(e.target.value)}
            placeholder="模型名，如 qwen2.5:3b 或 namespace/model:tag"
          />
          <Button type="primary" loading={pullMutation.isPending} onClick={() => pullMutation.mutate()}>
            拉取 / 下载
          </Button>
          <Button onClick={() => selectMutation.mutate(pullName.trim())}>仅设为当前模型</Button>
        </Space.Compact>
        <Typography.Paragraph type="secondary" style={{ marginTop: 8 }}>
          等价命令行：<code>ollama pull {pullName || "qwen2.5:3b"}</code>
          。拉取可能较久，请保持 Ollama 运行。
        </Typography.Paragraph>
      </Card>
    </Space>
  );
}
