import { Alert, Card, Col, Descriptions, List, Row, Space, Spin, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchHealth, fetchMeta } from "../api/health";
import { formatDateTime } from "../utils/format";

export default function HomePage() {
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: fetchHealth, refetchInterval: 15000 });
  const metaQuery = useQuery({ queryKey: ["meta"], queryFn: fetchMeta });

  const loading = healthQuery.isLoading || metaQuery.isLoading;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="当前为阶段一：项目骨架"
        description="已完成 FastAPI、React、SQLite、健康检查与基础页面。自然语言解析、真实招投标采集与 Word 报告将在后续阶段实现。本阶段不展示伪造的招标结果。"
      />

      {loading ? (
        <Spin tip="加载系统状态…" />
      ) : (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <Card title="服务健康" className="page-card">
              {healthQuery.isError ? (
                <Alert
                  type="error"
                  showIcon
                  message="无法连接后端"
                  description="请确认已启动 FastAPI（默认 http://127.0.0.1:8000），并检查 CORS / 代理配置。"
                />
              ) : (
                healthQuery.data && (
                  <Descriptions column={1} size="small" bordered>
                    <Descriptions.Item label="状态">
                      <Tag color={healthQuery.data.status === "ok" ? "success" : "warning"}>
                        {healthQuery.data.status}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="应用">{healthQuery.data.app}</Descriptions.Item>
                    <Descriptions.Item label="版本">{healthQuery.data.version}</Descriptions.Item>
                    <Descriptions.Item label="阶段">{healthQuery.data.phase}</Descriptions.Item>
                    <Descriptions.Item label="时区">{healthQuery.data.timezone}</Descriptions.Item>
                    <Descriptions.Item label="服务器时间">
                      {formatDateTime(healthQuery.data.time)}
                    </Descriptions.Item>
                    <Descriptions.Item label="数据库">
                      <Tag color={healthQuery.data.database_ok ? "success" : "error"}>
                        {healthQuery.data.database}
                      </Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="说明">{healthQuery.data.message}</Descriptions.Item>
                  </Descriptions>
                )
              )}
            </Card>
          </Col>
          <Col xs={24} lg={12}>
            <Card title="系统元信息" className="page-card">
              {metaQuery.data && (
                <>
                  <Typography.Paragraph>{metaQuery.data.description}</Typography.Paragraph>
                  <Typography.Text strong>已就绪</Typography.Text>
                  <List
                    size="small"
                    dataSource={metaQuery.data.features_ready}
                    renderItem={(item) => <List.Item>{item}</List.Item>}
                  />
                  <Typography.Text strong>规划中</Typography.Text>
                  <List
                    size="small"
                    dataSource={metaQuery.data.features_planned}
                    renderItem={(item) => <List.Item>{item}</List.Item>}
                  />
                </>
              )}
            </Card>
          </Col>
        </Row>
      )}
    </Space>
  );
}
