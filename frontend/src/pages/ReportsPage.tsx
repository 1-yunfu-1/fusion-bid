import { Alert, Button, Card, Table, Tag, Typography, message } from "antd";
import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { formatDateTime } from "../utils/format";

function downloadUrl(filename: string): string {
  const base = import.meta.env.VITE_API_BASE_URL || "";
  return `${base}/api/reports/download/${encodeURIComponent(filename)}`;
}

const reportStatuses: Record<string, { label: string; color: string }> = {
  success: { label: "成功", color: "success" },
  partial: { label: "部分成功", color: "warning" },
  failed: { label: "失败", color: "error" },
};

export default function ReportsPage() {
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["reports"],
    queryFn: async () => {
      const { data } = await apiClient.get("/api/reports");
      return data as {
        items: Array<{
          filename: string;
          size: number;
          modified_at?: number;
          original_query?: string;
          execution_id?: string;
          task_id?: string;
          status?: string;
          incremental_count?: number;
          finished_at?: string;
          exists: boolean;
        }>;
        total: number;
      };
    },
  });

  return (
    <Card
      title={<Typography.Title level={1} style={{ margin: 0, fontSize: 26 }}>报告中心</Typography.Title>}
      className="page-card"
      extra={
        <Button onClick={() => refetch()} loading={isFetching}>
          刷新
        </Button>
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="Word 报告"
        description="每次任务执行都会生成报告，命名为「原始问题_yyyyMMddHHmm.docx」。定时场景下报告默认仅含新增/内容更新条目。下载链接为可点击文件。"
      />
      <Table
        loading={isLoading}
        rowKey="filename"
        dataSource={data?.items || []}
        pagination={{ pageSize: 20, total: data?.total || 0 }}
        scroll={{ x: 980 }}
        columns={[
          {
            title: "文件名",
            dataIndex: "filename",
            ellipsis: true,
            render: (f: string) => <Typography.Text code>{f}</Typography.Text>,
          },
          {
            title: "关联问题",
            dataIndex: "original_query",
            ellipsis: true,
            render: (q?: string) => q || "—",
          },
          {
            title: "增量条数",
            dataIndex: "incremental_count",
            width: 90,
            render: (v?: number) => (v == null ? "—" : v),
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (s?: string) => {
              if (!s) return "—";
              const meta = reportStatuses[s] || { label: s, color: "default" };
              return <Tag color={meta.color}>{meta.label}</Tag>;
            },
          },
          {
            title: "大小",
            dataIndex: "size",
            width: 100,
            render: (n: number) => (n ? `${(n / 1024).toFixed(1)} KB` : "—"),
          },
          {
            title: "完成时间",
            dataIndex: "finished_at",
            width: 170,
            render: (
              v: string | undefined,
              r: { modified_at?: number; exists: boolean; filename: string },
            ) =>
              v
                ? formatDateTime(v)
                : r.modified_at
                  ? formatDateTime(new Date(r.modified_at * 1000).toISOString())
                  : "—",
          },
          {
            title: "操作",
            key: "act",
            width: 100,
            render: (
              _: unknown,
              r: { exists: boolean; filename: string },
            ) =>
              r.exists ? (
                <Button
                  type="link"
                  href={downloadUrl(r.filename)}
                  target="_blank"
                  onClick={() => message.success("开始下载")}
                >
                  下载
                </Button>
              ) : (
                <Tag color="error">文件缺失</Tag>
              ),
          },
        ]}
      />
    </Card>
  );
}
