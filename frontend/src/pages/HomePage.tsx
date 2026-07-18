import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { ArrowRightOutlined, CheckCircleOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { apiClient } from "../api/client";
import { fetchHealth, fetchMeta } from "../api/health";
import { formatDateTime } from "../utils/format";

type SourceInfo = {
  source_name: string;
  display_name: string;
  requires_login: boolean;
  enabled: boolean;
  data_mode: string;
};

export default function HomePage() {
  const healthQuery = useQuery({
    queryKey: ["health"],
    queryFn: fetchHealth,
    refetchInterval: 15000,
  });
  const metaQuery = useQuery({ queryKey: ["meta"], queryFn: fetchMeta });
  const sourceQuery = useQuery({
    queryKey: ["sources"],
    queryFn: async () => {
      const { data } = await apiClient.get<{ items: SourceInfo[] }>("/api/sources");
      return data;
    },
  });

  const loading = healthQuery.isLoading || metaQuery.isLoading || sourceQuery.isLoading;
  const publicSources = sourceQuery.data?.items.filter((source) => !source.requires_login) || [];
  const loginSource = sourceQuery.data?.items.find((source) => source.requires_login);
  const ready = healthQuery.data?.status === "ok";

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Card className="page-card home-hero">
        <Row gutter={[24, 20]} align="middle">
          <Col xs={24} lg={17}>
            <Tag color={ready ? "success" : "warning"} icon={<CheckCircleOutlined />}>
              {ready ? "系统运行就绪" : "系统降级运行"}
            </Tag>
            <Typography.Title level={1} style={{ margin: "12px 0 8px" }}>
              用一句话完成招投标信息聚合
            </Typography.Title>
            <Typography.Paragraph type="secondary" style={{ fontSize: 16, marginBottom: 0 }}>
              输入需求后确认关键词、区域和日期，系统会自动执行首轮检索、去重并生成可下载的 Word 报告；定时任务继续交付后续增量。
            </Typography.Paragraph>
          </Col>
          <Col xs={24} lg={7} style={{ textAlign: "center" }}>
            <Link to="/tasks/new">
              <Button type="primary" size="large" icon={<ArrowRightOutlined />} iconPosition="end">
                开始检索
              </Button>
            </Link>
          </Col>
        </Row>
      </Card>

      {loading ? (
        <Spin tip="加载系统状态…" />
      ) : (
        <>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={8}>
              <Card title="公开数据源" className="page-card" style={{ height: "100%" }}>
                <Typography.Paragraph>
                  无需登录，某个来源失败时其余来源继续执行。
                </Typography.Paragraph>
                <Space wrap>
                  {publicSources.map((source) => (
                    <Tag color={source.enabled ? "success" : "default"} key={source.source_name}>
                      {source.display_name}
                    </Tag>
                  ))}
                </Space>
              </Card>
            </Col>
            <Col xs={24} md={8}>
              <Card title="登录态数据源" className="page-card" style={{ height: "100%" }}>
                <Typography.Paragraph>
                  使用可见浏览器手工登录；配置异常或登录失效不会阻塞公开源。
                </Typography.Paragraph>
                <Tag color={loginSource?.enabled ? "processing" : "warning"}>
                  {loginSource?.enabled ? "已启用，登录状态需单独检查" : "当前已安全停用"}
                </Tag>
              </Card>
            </Col>
            <Col xs={24} md={8}>
              <Card title="解析降级能力" className="page-card" style={{ height: "100%" }}>
                <Typography.Paragraph>
                  在线模型或 Ollama 不可用时自动切换规则解析，确认流程仍可继续。
                </Typography.Paragraph>
                <Tag color="blue">API → Ollama → 规则</Tag>
              </Card>
            </Col>
          </Row>

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={12}>
              <Card title="运行状态" className="page-card">
                {healthQuery.isError ? (
                  <Alert
                    type="error"
                    showIcon
                    message="无法连接后端"
                    description="请确认 FastAPI 已启动，并检查代理配置。"
                  />
                ) : healthQuery.data ? (
                  <Descriptions column={1} size="small" bordered>
                    <Descriptions.Item label="服务">
                      <Tag color={ready ? "success" : "warning"}>{healthQuery.data.status}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="版本">{healthQuery.data.version}</Descriptions.Item>
                    <Descriptions.Item label="交付标识">{healthQuery.data.phase}</Descriptions.Item>
                    <Descriptions.Item label="时区">{healthQuery.data.timezone}</Descriptions.Item>
                    <Descriptions.Item label="服务器时间">
                      {formatDateTime(healthQuery.data.time)}
                    </Descriptions.Item>
                    <Descriptions.Item label="数据库">
                      <Tag color={healthQuery.data.database_ok ? "success" : "error"}>
                        {healthQuery.data.database}
                      </Tag>
                    </Descriptions.Item>
                  </Descriptions>
                ) : null}
              </Card>
            </Col>
            <Col xs={24} lg={12}>
              <Card title="本次交付能力" className="page-card">
                <Typography.Paragraph>{metaQuery.data?.description}</Typography.Paragraph>
                <Space wrap>
                  {[
                    "意图确认后自动首查",
                    "多源失败隔离",
                    "跨源去重与增量",
                    "Word 报告下载",
                    "单次/日/周/月调度",
                    "live / fixture 可追溯",
                  ].map((feature) => <Tag key={feature}>{feature}</Tag>)}
                </Space>
              </Card>
            </Col>
          </Row>
        </>
      )}
    </Space>
  );
}
