import { Alert, Card, Table, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { formatDateTime } from "../utils/format";

export default function AnnouncementsPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["announcements"],
    queryFn: async () => {
      const { data } = await apiClient.get("/api/announcements");
      return data as {
        items: Array<{
          id: string;
          title: string;
          source_name: string;
          source_url: string;
          data_mode: string;
          region?: string;
          summary?: string;
          publish_time?: string;
          crawl_time?: string;
          attachment_links?: string[];
        }>;
        total: number;
      };
    },
  });

  return (
    <Card title="采集结果" className="page-card">
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="请注意 data_mode 标注"
        description="live=真实抓取；fixture=演示/测试数据。摘要基于网页原文抽取，不编造预算与截止时间。"
      />
      <Table
        loading={isLoading}
        rowKey="id"
        dataSource={data?.items || []}
        pagination={{ total: data?.total || 0 }}
        columns={[
          {
            title: "模式",
            dataIndex: "data_mode",
            width: 90,
            render: (m: string) => (
              <Tag color={m === "live" ? "green" : "gold"}>{m}</Tag>
            ),
          },
          {
            title: "标题",
            dataIndex: "title",
            ellipsis: true,
            render: (t: string, r) => (
              <Typography.Link href={r.source_url} target="_blank" rel="noreferrer">
                {t}
              </Typography.Link>
            ),
          },
          { title: "来源", dataIndex: "source_name", width: 100 },
          { title: "区域", dataIndex: "region", width: 100 },
          {
            title: "发布时间",
            dataIndex: "publish_time",
            width: 160,
            render: (v: string) => formatDateTime(v),
          },
          {
            title: "摘要",
            dataIndex: "summary",
            ellipsis: true,
          },
          {
            title: "附件",
            dataIndex: "attachment_links",
            width: 80,
            render: (v: string[]) => (v && v.length ? v.length : 0),
          },
          {
            title: "合并来源",
            dataIndex: "related_sources",
            width: 100,
            render: (v: unknown[]) => (v && v.length ? <Tag color="purple">{v.length}</Tag> : "—"),
          },
        ]}
      />
    </Card>
  );
}
