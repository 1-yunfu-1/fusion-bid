import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  InputNumber,
  List,
  Progress,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

interface SourceItem {
  source_name: string;
  display_name: string;
  requires_login: boolean;
  enabled: boolean;
  official?: boolean;
}

interface LoginStatus {
  enabled: boolean;
  login_url: string;
  home_url?: string;
  state: { exists: boolean; path: string; message: string; size?: number; modified_at?: string };
  health: { ok: boolean; message: string; login_ok?: boolean | null } | null;
  instructions: string[];
  cli: string;
  launcher?: {
    process_running?: boolean;
    pid?: number | null;
    elapsed_seconds?: number | null;
    wait_seconds?: number;
    last_error?: string | null;
  };
}

export default function SourcesPage() {
  const qc = useQueryClient();
  const [loginUrl, setLoginUrl] = useState("");
  const [waitSeconds, setWaitSeconds] = useState(600);

  const listQuery = useQuery({
    queryKey: ["sources"],
    queryFn: async () => {
      const { data } = await apiClient.get<{ items: SourceItem[] }>("/api/sources");
      return data;
    },
  });

  const loginQuery = useQuery({
    queryKey: ["login-status"],
    queryFn: async () => {
      const { data } = await apiClient.get<LoginStatus>("/api/login/status");
      return data;
    },
    // 登录进程运行中时轮询
    refetchInterval: (query) =>
      query.state.data?.launcher?.process_running ||
      (query.state.data && !query.state.data.state?.exists)
        ? 3000
        : 15000,
  });

  useEffect(() => {
    if (loginQuery.data?.login_url && !loginUrl) {
      setLoginUrl(loginQuery.data.login_url);
    }
  }, [loginQuery.data, loginUrl]);

  // 进程从 running -> 结束且出现 state 时提示
  useEffect(() => {
    if (loginQuery.data?.state?.exists && loginQuery.data.launcher?.process_running === false) {
      // no-op; user sees tags update
    }
  }, [loginQuery.data]);

  const healthMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post("/api/sources/health-all");
      return data as {
        results: {
          source_name: string;
          ok: boolean;
          message: string;
          requires_login: boolean;
        }[];
      };
    },
    onSuccess: () => {
      message.success("健康检查完成");
      qc.invalidateQueries({ queryKey: ["login-status"] });
    },
    onError: () => message.error("健康检查失败"),
  });

  const startLoginMutation = useMutation({
    mutationFn: async (vars: { force?: boolean }) => {
      const { data } = await apiClient.post("/api/login/start", {
        login_url: null,
        wait_seconds: waitSeconds,
        force: !!vars.force,
      });
      return data as {
        ok: boolean;
        message: string;
        hint?: string;
        pid?: number;
        wait_seconds?: number;
        mode?: string;
      };
    },
    onSuccess: (data) => {
      message.success(data.message || "已启动登录");
      if (data.hint) message.info(data.hint, 6);
      qc.invalidateQueries({ queryKey: ["login-status"] });
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      message.error(
        typeof err.response?.data?.detail === "string"
          ? err.response.data.detail
          : err.message || "启动失败",
      );
    },
  });

  const stopLoginMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post("/api/login/stop");
      return data as { ok: boolean; message: string };
    },
    onSuccess: (data) => {
      message.info(data.message);
      qc.invalidateQueries({ queryKey: ["login-status"] });
    },
  });

  const clearStateMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.delete("/api/login/state");
      return data as { ok: boolean; message: string };
    },
    onSuccess: (data) => {
      message.success(data.message);
      qc.invalidateQueries({ queryKey: ["login-status"] });
    },
  });

  const openScriptsMutation = useMutation({
    mutationFn: async () => {
      const { data } = await apiClient.post("/api/login/open-scripts");
      return data as { ok: boolean; message: string; path?: string };
    },
    onSuccess: (data) => {
      if (data.ok) message.success(data.message);
      else message.warning(data.message + (data.path ? ` 路径: ${data.path}` : ""));
    },
  });

  const healthMap = new Map(
    (healthMutation.data?.results || []).map((r) => [r.source_name, r]),
  );

  const launcher = loginQuery.data?.launcher;
  const running = !!launcher?.process_running;
  const waitSec = launcher?.wait_seconds || waitSeconds;
  const elapsed = launcher?.elapsed_seconds || 0;
  const progress = running && waitSec > 0 ? Math.min(100, Math.round((elapsed / waitSec) * 100)) : 0;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={1} style={{ margin: 0 }}>数据源</Typography.Title>
      <Alert
        type="info"
        showIcon
        message="公开源 + 登录态源"
        description="公开：ccgp、cebpub。登录态：login_portal。可在本页一键启动登录浏览器；登录失败不阻塞公开源。"
      />

      <Card
        title="登录态招采门户"
        className="page-card"
        loading={loginQuery.isLoading}
        extra={
          <Space wrap>
            <Button
              type="primary"
              loading={startLoginMutation.isPending}
              onClick={() => startLoginMutation.mutate({ force: false })}
              disabled={running || !loginQuery.data?.enabled}
            >
              启动登录浏览器
            </Button>
            <Button
              loading={startLoginMutation.isPending}
              onClick={() => startLoginMutation.mutate({ force: true })}
              disabled={!loginQuery.data?.enabled}
            >
              强制重新启动
            </Button>
            <Button
              danger
              disabled={!running}
              loading={stopLoginMutation.isPending}
              onClick={() => stopLoginMutation.mutate()}
            >
              停止
            </Button>
          </Space>
        }
      >
        {loginQuery.data && (
          <>
            <Space wrap style={{ marginBottom: 12 }}>
              <Tag color={loginQuery.data.enabled ? "blue" : "default"}>
                {loginQuery.data.enabled ? "源已启用" : "源已禁用"}
              </Tag>
              <Tag color={loginQuery.data.state.exists ? "success" : "orange"}>
                {loginQuery.data.state.exists ? "已有登录态文件" : "无登录态文件"}
              </Tag>
              <Tag color={running ? "processing" : "default"}>
                {running ? `登录进程运行中 pid=${launcher?.pid ?? "?"}` : "登录进程未运行"}
              </Tag>
              {loginQuery.data.health && (
                <Tag color={loginQuery.data.health.ok ? "success" : "error"}>
                  健康: {loginQuery.data.health.ok ? "ok" : "fail"}
                </Tag>
              )}
            </Space>

            {running && (
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message="请切换到弹出的浏览器完成登录"
                description={
                  <div>
                    <p style={{ marginBottom: 8 }}>
                      若 chinabidding 被安全拦截，请在地址栏改开你能登录的招采网站。登录成功后在弹出的黑色控制台按
                      Enter，或等待自动保存。
                    </p>
                    <Progress percent={progress} size="small" status="active" />
                    <Typography.Text type="secondary">
                      已等待 {elapsed}s / 最长 {waitSec}s
                    </Typography.Text>
                  </div>
                }
              />
            )}

            {loginQuery.data.state.exists && (
              <Alert
                type="success"
                showIcon
                style={{ marginBottom: 12 }}
                message="已检测到登录态文件"
                description={`路径：${loginQuery.data.state.path}${
                  loginQuery.data.state.modified_at
                    ? ` · 更新：${loginQuery.data.state.modified_at}`
                    : ""
                }`}
              />
            )}

            {loginQuery.data.health?.message && !running && (
              <Alert
                type={loginQuery.data.health.ok ? "success" : "warning"}
                showIcon
                message={loginQuery.data.health.message}
                style={{ marginBottom: 12 }}
              />
            )}

            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              登录门户（由环境配置统一管理）
            </Typography.Paragraph>
            <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
              <Input
                value={loginUrl}
                readOnly
                placeholder="请在环境配置中设置登录门户"
              />
              <InputNumber
                min={60}
                max={1800}
                value={waitSeconds}
                onChange={(v) => setWaitSeconds(Number(v) || 600)}
                addonBefore="等待秒"
                style={{ width: 160 }}
              />
            </Space.Compact>

            <Space wrap style={{ marginBottom: 16 }}>
              <Button
                onClick={() => {
                  qc.invalidateQueries({ queryKey: ["login-status"] });
                  message.info("已刷新状态");
                }}
              >
                刷新状态
              </Button>
              <Button
                onClick={async () => {
                  try {
                    const { data } = await apiClient.post("/api/sources/login_portal/health");
                    message.info((data as { message: string }).message);
                    qc.invalidateQueries({ queryKey: ["login-status"] });
                  } catch {
                    message.error("检查失败");
                  }
                }}
              >
                检查 login_portal
              </Button>
              <Button
                danger
                disabled={!loginQuery.data.state.exists}
                loading={clearStateMutation.isPending}
                onClick={() => clearStateMutation.mutate()}
              >
                清除登录态
              </Button>
              <Button
                loading={openScriptsMutation.isPending}
                onClick={() => openScriptsMutation.mutate()}
              >
                打开 scripts 目录
              </Button>
            </Space>

            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message="说明"
              description="普通浏览器里登录不会同步到本系统。必须通过本页「启动登录浏览器」或 scripts\run_login_init.bat，在弹出的 Playwright/Chrome 窗口登录并保存。"
            />

            <Typography.Text strong>备用步骤</Typography.Text>
            <List
              size="small"
              dataSource={loginQuery.data.instructions}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />
            <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>
              CLI：<Typography.Text code>{loginQuery.data.cli}</Typography.Text>
              {" · "}
              或双击 <Typography.Text code>scripts\run_login_init.bat</Typography.Text>
            </Typography.Paragraph>
          </>
        )}
      </Card>

      <Card
        title="数据源列表"
        className="page-card"
        extra={
          <Button type="primary" loading={healthMutation.isPending} onClick={() => healthMutation.mutate()}>
            全部健康检查
          </Button>
        }
      >
        <Table
          loading={listQuery.isLoading}
          rowKey="source_name"
          dataSource={listQuery.data?.items || []}
          pagination={false}
          scroll={{ x: 880 }}
          columns={[
            { title: "标识", dataIndex: "source_name" },
            { title: "名称", dataIndex: "display_name" },
            {
              title: "登录",
              dataIndex: "requires_login",
              render: (v: boolean) => (v ? <Tag color="orange">需要</Tag> : <Tag>公开</Tag>),
            },
            {
              title: "启用",
              dataIndex: "enabled",
              render: (v: boolean) => (v ? <Tag color="success">是</Tag> : <Tag>否</Tag>),
            },
            {
              title: "健康",
              key: "health",
              render: (_, r) => {
                const h = healthMap.get(r.source_name);
                if (r.source_name === "login_portal" && loginQuery.data?.health && !h) {
                  const lh = loginQuery.data.health;
                  return (
                    <Space direction="vertical" size={0}>
                      <Tag color={lh.ok ? "success" : "error"}>{lh.ok ? "ok" : "fail"}</Tag>
                      <span className="muted">{lh.message}</span>
                    </Space>
                  );
                }
                if (!h) return <span className="muted">未检查</span>;
                return (
                  <Space direction="vertical" size={0}>
                    <Tag color={h.ok ? "success" : "error"}>{h.ok ? "ok" : "fail"}</Tag>
                    <span className="muted">{h.message}</span>
                  </Space>
                );
              },
            },
            {
              title: "操作",
              key: "act",
              width: 220,
              render: (_, r) => (
                <Space wrap size={0}>
                  <Button
                    size="small"
                    type="link"
                    onClick={async () => {
                      try {
                        const { data } = await apiClient.post(`/api/sources/${r.source_name}/health`);
                        message.info(`${r.source_name}: ${(data as { message: string }).message}`);
                        qc.invalidateQueries({ queryKey: ["login-status"] });
                      } catch {
                        message.error("检查失败");
                      }
                    }}
                  >
                    检查
                  </Button>
                  {r.requires_login && (
                    <Button
                      size="small"
                      type="link"
                      loading={startLoginMutation.isPending}
                      onClick={() => startLoginMutation.mutate({ force: false })}
                    >
                      登录初始化
                    </Button>
                  )}
                </Space>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
