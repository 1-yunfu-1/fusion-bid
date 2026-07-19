import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Drawer,
  Form,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  TimePicker,
  Typography,
  message,
} from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import dayjs, { type Dayjs } from "dayjs";
import { apiClient, apiResourceUrl } from "../api/client";
import { executeTask, listTaskExecutions, listTasks, updateTask } from "../api/tasks";
import type { ParsedIntent } from "../types/intent";
import type { TaskExecutionResponse, TaskOut } from "../types/task";
import { formatDateTime } from "../utils/format";

const { TextArea } = Input;

const taskStatusMeta: Record<string, { label: string; color: string }> = {
  confirmed: { label: "待执行", color: "blue" },
  scheduled: { label: "已计划", color: "purple" },
  running: { label: "执行中", color: "processing" },
  done: { label: "已完成", color: "success" },
  failed: { label: "执行失败", color: "error" },
  paused: { label: "已暂停", color: "default" },
  expired: { label: "已过期", color: "warning" },
};

const executionStatusMeta: Record<string, { label: string; color: string }> = {
  success: { label: "成功", color: "success" },
  partial: { label: "部分成功", color: "warning" },
  failed: { label: "失败", color: "error" },
  running: { label: "执行中", color: "processing" },
};

const triggerLabels: Record<string, string> = {
  initial: "创建后首轮",
  manual: "手工执行",
  scheduled: "定时执行",
};

const collectionFailureLabels: Record<string, string> = {
  invalid_pdf: "无效/损坏 PDF",
  transient_pdf_timeout: "PDF 临时未就绪",
  pdf_incomplete: "PDF 不完整",
  ocr_failure: "OCR 失败",
  invalid_pdf_cooldown: "损坏 PDF 冷却跳过",
  site_blocked: "站点阻断",
  browser_failure: "浏览器失败",
  not_attempted: "未尝试",
};

const scheduleLabels: Record<string, string> = {
  once: "单次",
  daily: "每日",
  weekly: "每周",
  monthly: "每月",
};

function statusTag(status: string, execution = false) {
  const meta = (execution ? executionStatusMeta : taskStatusMeta)[status] || {
    label: status,
    color: "default",
  };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function taskToForm(task: TaskOut) {
  const pi = (task.parsed_intent || {}) as Record<string, unknown>;
  const exclude =
    (pi.exclude_keywords as string[] | undefined) ||
    ((pi as { exclude_keywords?: string[] }).exclude_keywords) ||
    [];
  const dateRange = (pi.date_range as { original_expression?: string } | undefined) || {};
  return {
    original_query: task.original_query,
    keywords: task.keywords || [],
    exclude_keywords: exclude,
    regions: task.regions || [],
    start_date: task.start_date ? dayjs(task.start_date) : null,
    end_date: task.end_date ? dayjs(task.end_date) : null,
    original_expression: dateRange.original_expression || null,
    schedule_enabled: task.schedule_enabled,
    schedule_type: task.schedule_type || undefined,
    execute_date: task.execute_date ? dayjs(task.execute_date) : null,
    execute_time: task.execute_time ? dayjs(task.execute_time, "HH:mm") : null,
    execute_immediately: task.execute_immediately,
  };
}

function formToIntent(values: Record<string, unknown>, timezone = "Asia/Shanghai"): ParsedIntent {
  const start = values.start_date as Dayjs | null;
  const end = values.end_date as Dayjs | null;
  const execDate = values.execute_date as Dayjs | null;
  const execTime = values.execute_time as Dayjs | null;
  return {
    original_query: String(values.original_query || ""),
    keywords: (values.keywords as string[]) || [],
    exclude_keywords: (values.exclude_keywords as string[]) || [],
    regions: (values.regions as string[]) || [],
    date_range: {
      start_date: start ? start.format("YYYY-MM-DD") : null,
      end_date: end ? end.format("YYYY-MM-DD") : null,
      original_expression: (values.original_expression as string) || null,
    },
    schedule: {
      enabled: Boolean(values.schedule_enabled),
      schedule_type:
        (values.schedule_type as ParsedIntent["schedule"]["schedule_type"]) || null,
      execute_date: execDate ? execDate.format("YYYY-MM-DD") : null,
      execute_time: execTime ? execTime.format("HH:mm") : null,
      timezone,
    },
    execute_immediately: Boolean(values.execute_immediately),
  };
}

export default function TasksPage() {
  const qc = useQueryClient();
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<TaskOut | null>(null);
  const [historyTask, setHistoryTask] = useState<TaskOut | null>(null);
  const [lastExecution, setLastExecution] = useState<TaskExecutionResponse | null>(null);
  const [form] = Form.useForm();

  const { data, isLoading, isError } = useQuery({
    queryKey: ["tasks"],
    queryFn: listTasks,
  });

  const historyQuery = useQuery({
    queryKey: ["task-executions", historyTask?.id],
    queryFn: () => listTaskExecutions(historyTask!.id),
    enabled: Boolean(historyTask),
  });

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["tasks"] });
    qc.invalidateQueries({ queryKey: ["announcements"] });
    qc.invalidateQueries({ queryKey: ["reports"] });
    qc.invalidateQueries({ queryKey: ["task-executions"] });
  };

  const openEdit = (task: TaskOut) => {
    setEditing(task);
    form.setFieldsValue(taskToForm(task));
    setEditOpen(true);
  };

  const closeEdit = () => {
    setEditOpen(false);
    setEditing(null);
    form.resetFields();
  };

  const saveMutation = useMutation({
    mutationFn: async () => {
      if (!editing) throw new Error("无编辑任务");
      const values = await form.validateFields();
      const intent = formToIntent(values, editing.timezone || "Asia/Shanghai");
      return updateTask(editing.id, intent, false);
    },
    onSuccess: (res) => {
      message.success(res.message || "任务已更新");
      if (res.issues?.length) {
        const warns = res.issues.filter((i) => i.severity === "warning");
        if (warns.length) {
          message.warning(warns.map((w) => w.message).join("；"));
        }
      }
      closeEdit();
      refresh();
    },
    onError: (e: unknown) => {
      const err = e as {
        response?: { data?: { detail?: string | { message?: string; issues?: { message: string }[] } } };
        message?: string;
      };
      const detail = err.response?.data?.detail;
      if (typeof detail === "string") {
        message.error(detail);
      } else if (detail && typeof detail === "object") {
        message.error(detail.message || "保存失败");
        if (detail.issues?.length) {
          message.warning(detail.issues.map((i) => i.message).join("；"));
        }
      } else {
        message.error(err.message || "保存失败");
      }
    },
  });

  const execMutation = useMutation({
    mutationFn: ({ taskId, reportMode }: { taskId: string; reportMode: "incremental" | "full_snapshot" }) =>
      executeTask(taskId, "manual", reportMode),
    onSuccess: (res) => {
      setLastExecution(res);
      if (res.status === "success") message.success("任务执行成功");
      else if (res.status === "partial") message.warning("任务部分成功，请查看失败来源");
      else message.error("任务执行失败，可查看执行记录后重试");
      refresh();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } }; message?: string };
      message.error(
        typeof err.response?.data?.detail === "string"
          ? err.response.data.detail
          : err.message || "执行失败",
      );
    },
  });

  const pauseMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.post(`/api/tasks/${id}/pause`);
      return data;
    },
    onSuccess: () => {
      message.success("已暂停");
      refresh();
    },
    onError: () => message.error("暂停失败"),
  });

  const resumeMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.post(`/api/tasks/${id}/resume`);
      return data;
    },
    onSuccess: () => {
      message.success("已恢复");
      refresh();
    },
    onError: (e: unknown) => {
      const err = e as { response?: { data?: { detail?: string } } };
      message.error(
        typeof err.response?.data?.detail === "string"
          ? err.response.data.detail
          : "恢复失败",
      );
    },
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const { data } = await apiClient.delete(`/api/tasks/${id}`);
      return data;
    },
    onSuccess: () => {
      message.success("已删除");
      refresh();
    },
    onError: () => message.error("删除失败"),
  });

  return (
    <Card
      title={<Typography.Title level={1} style={{ margin: 0, fontSize: 26 }}>任务列表</Typography.Title>}
      className="page-card"
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="采集执行与定时调度"
        description="支持编辑已创建任务、立即执行、暂停/恢复定时、软删除。编辑后调度会自动同步；定时任务到点自动执行并生成增量 Word 报告。"
      />
      {isError && <Alert type="error" message="加载失败，请确认后端已启动" />}
      {lastExecution && (
        <Alert
          type={
            lastExecution.status === "success"
              ? "success"
              : lastExecution.status === "partial"
                ? "warning"
                : "error"
          }
          showIcon
          closable
          onClose={() => setLastExecution(null)}
          style={{ marginBottom: 16 }}
          message={
            <Space wrap>
              <span>最近手工执行：{executionStatusMeta[lastExecution.status]?.label || lastExecution.status}</span>
              <Tag>原始 {lastExecution.raw_result_count}</Tag>
              <Tag>入库 {lastExecution.saved_count}</Tag>
              <Tag>增量 {lastExecution.incremental_count}</Tag>
              <Tag>{lastExecution.report_mode === "full_snapshot" ? "未去重完整快照" : "增量交付"}</Tag>
              {lastExecution.truncated && <Tag color="error">已达安全上限，结果截断</Tag>}
            </Space>
          }
          description={
            <Space direction="vertical" size="small">
              {Object.keys(lastExecution.sources_failed).length > 0 && (
                <span>
                  失败来源：
                  {Object.entries(lastExecution.sources_failed)
                    .map(([source, reason]) => `${source}（${reason}）`)
                    .join("；")}
                </span>
              )}
              {lastExecution.error_message && <span>{lastExecution.error_message}</span>}
              {lastExecution.analysis_preview?.portfolio_summary && (
                <span>
                  投标决策分析：{lastExecution.analysis_preview.portfolio_summary}
                </span>
              )}
              {lastExecution.report_download_url && (
                <Button
                  type="link"
                  style={{ padding: 0 }}
                  href={apiResourceUrl(lastExecution.report_download_url)}
                >
                  下载本次 Word 报告
                </Button>
              )}
            </Space>
          }
        />
      )}
      <Table
        loading={isLoading}
        rowKey="id"
        dataSource={data?.items || []}
        pagination={{ total: data?.total || 0, pageSize: 20 }}
        scroll={{ x: 1180 }}
        columns={[
          {
            title: "原始问题",
            dataIndex: "original_query",
            ellipsis: true,
            width: 220,
            render: (t: string) => <Typography.Text>{t}</Typography.Text>,
          },
          {
            title: "关键词",
            dataIndex: "keywords",
            width: 120,
            render: (v: string[] | null) => (v || []).map((k) => <Tag key={k}>{k}</Tag>),
          },
          {
            title: "区域",
            dataIndex: "regions",
            width: 100,
            render: (v: string[] | null) => (v || []).map((k) => <Tag key={k}>{k}</Tag>),
          },
          {
            title: "周期",
            key: "period",
            width: 160,
            render: (_: unknown, r: TaskOut) =>
              r.start_date || r.end_date ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {r.start_date || "—"} ~ {r.end_date || "—"}
                </Typography.Text>
              ) : (
                "—"
              ),
          },
          {
            title: "调度",
            key: "sch",
            width: 140,
            render: (_: unknown, r: TaskOut) =>
              r.schedule_enabled ? (
                <Space direction="vertical" size={0}>
                  <Tag color={r.is_paused ? "default" : "purple"}>
                    {scheduleLabels[r.schedule_type || ""] || r.schedule_type} {r.execute_time}
                    {r.is_paused ? "（暂停）" : ""}
                  </Tag>
                  {r.next_run_at && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      下次: {formatDateTime(r.next_run_at)}
                    </Typography.Text>
                  )}
                </Space>
              ) : (
                <Tag>立即</Tag>
              ),
          },
          {
            title: "状态",
            dataIndex: "status",
            width: 100,
            render: (s: string) => statusTag(s),
          },
          {
            title: "上次运行",
            dataIndex: "last_run_at",
            width: 160,
            render: (v?: string | null) => formatDateTime(v || undefined),
          },
          {
            title: "操作",
            key: "act",
            fixed: "right",
            width: 430,
            render: (_: unknown, r: TaskOut) => (
              <Space wrap size={0}>
                <Button type="link" size="small" onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Button
                  type="link"
                  size="small"
                  loading={execMutation.isPending && execMutation.variables?.taskId === r.id}
                  disabled={execMutation.isPending && execMutation.variables?.taskId !== r.id}
                  onClick={() => execMutation.mutate({ taskId: r.id, reportMode: "incremental" })}
                >
                  立即执行
                </Button>
                <Button
                  type="link"
                  size="small"
                  loading={execMutation.isPending && execMutation.variables?.taskId === r.id && execMutation.variables?.reportMode === "full_snapshot"}
                  disabled={execMutation.isPending && execMutation.variables?.taskId !== r.id}
                  onClick={() => execMutation.mutate({ taskId: r.id, reportMode: "full_snapshot" })}
                >
                  重新采集未去重完整报告
                </Button>
                <Button type="link" size="small" onClick={() => setHistoryTask(r)}>
                  执行记录
                </Button>
                {r.schedule_enabled && !r.is_paused && (
                  <Button
                    type="link"
                    size="small"
                    onClick={() => pauseMutation.mutate(r.id)}
                    loading={pauseMutation.isPending}
                  >
                    暂停
                  </Button>
                )}
                {r.schedule_enabled && r.is_paused && (
                  <Button
                    type="link"
                    size="small"
                    onClick={() => resumeMutation.mutate(r.id)}
                    loading={resumeMutation.isPending}
                  >
                    恢复
                  </Button>
                )}
                <Button
                  type="link"
                  size="small"
                  danger
                  onClick={() => deleteMutation.mutate(r.id)}
                  loading={deleteMutation.isPending}
                >
                  删除
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={editing ? `编辑任务` : "编辑任务"}
        open={editOpen}
        onCancel={closeEdit}
        onOk={() => saveMutation.mutate()}
        confirmLoading={saveMutation.isPending}
        okText="保存"
        width={720}
        destroyOnClose
      >
        {editing && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            message={
              <Space>
                <span>任务 ID：{editing.id.slice(0, 8)}…</span>
                {statusTag(editing.status)}
              </Space>
            }
            description="可修改查询条件与调度。保存后立即生效；若启用定时且未暂停，将重新计算下次运行时间。"
          />
        )}
        <Form form={form} layout="vertical">
          <Form.Item
            name="original_query"
            label="原始问题"
            rules={[{ required: true, message: "请填写原始问题" }]}
          >
            <TextArea rows={2} />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} md={12}>
              <Form.Item
                name="keywords"
                label="关键词"
                rules={[{ required: true, type: "array", min: 1, message: "至少 1 个关键词" }]}
              >
                <Select mode="tags" placeholder="输入后回车" tokenSeparators={[",", "，"]} />
              </Form.Item>
            </Col>
            <Col xs={24} md={12}>
              <Form.Item
                name="regions"
                label="区域"
                rules={[{ required: true, type: "array", min: 1, message: "至少 1 个区域" }]}
              >
                <Select mode="tags" placeholder="如 全国、北京市、安徽省" tokenSeparators={[",", "，"]} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="exclude_keywords" label="排除词">
            <Select mode="tags" tokenSeparators={[",", "，"]} />
          </Form.Item>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item
                name="start_date"
                label="开始日期"
                rules={[{ required: true, message: "请选择开始日期" }]}
              >
                <DatePicker style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item
                name="end_date"
                label="结束日期"
                rules={[{ required: true, message: "请选择结束日期" }]}
              >
                <DatePicker style={{ width: "100%" }} />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="original_expression" label="时间原表达">
                <Input placeholder="如 最近1个月" />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="schedule_enabled" label="启用定时" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="schedule_type" label="频率">
                <Select
                  allowClear
                  options={[
                    { value: "once", label: "仅一次" },
                    { value: "daily", label: "每日" },
                    { value: "weekly", label: "每周" },
                    { value: "monthly", label: "每月" },
                  ]}
                />
              </Form.Item>
            </Col>
            <Col xs={24} md={8}>
              <Form.Item name="execute_time" label="执行时间">
                <TimePicker format="HH:mm" style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Form.Item name="execute_date" label="单次执行日期">
                <DatePicker style={{ width: "100%" }} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="execute_immediately" hidden valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={historyTask ? `执行记录 · ${historyTask.original_query}` : "执行记录"}
        open={Boolean(historyTask)}
        onClose={() => setHistoryTask(null)}
        width="min(760px, 100vw)"
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="每次首轮、手工和定时执行都会单独留痕"
          description="定时任务的任务状态保持“已计划”，最近一次成功或失败以这里的执行状态为准。"
        />
        <Table
          loading={historyQuery.isLoading}
          rowKey="id"
          dataSource={historyQuery.data?.items || []}
          pagination={{ pageSize: 10, total: historyQuery.data?.total || 0 }}
          scroll={{ x: 980 }}
          columns={[
            {
              title: "触发方式",
              dataIndex: "trigger_type",
              width: 110,
              render: (value: string, row) => (
                <Space direction="vertical" size={0}>
                  <span>{triggerLabels[value] || value}</span>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    {row.report_mode === "full_snapshot" ? "未去重快照" : "增量"}
                  </Typography.Text>
                </Space>
              ),
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 100,
              render: (value: string) => statusTag(value, true),
            },
            {
              title: "开始时间",
              dataIndex: "started_at",
              width: 170,
              render: (value?: string | null) => formatDateTime(value || undefined),
            },
            {
              title: "结果",
              key: "counts",
              width: 150,
              render: (_: unknown, row) => (
                <Typography.Text type="secondary">
                  原始 {row.raw_result_count} / 增量 {row.incremental_count}
                </Typography.Text>
              ),
            },
            {
              title: "说明",
              key: "note",
              ellipsis: true,
              render: (_: unknown, row) => row.error_message || row.analysis_preview?.portfolio_summary || "—",
            },
            {
              title: "详情诊断",
              key: "detail-diagnostics",
              width: 280,
              render: (_: unknown, row) => {
                const failures = Object.entries(row.failure_breakdown || {});
                const bySource = Object.entries(row.failure_breakdown_by_source || {});
                return failures.length ? (
                  <Space direction="vertical" size={2}>
                    <Space size={[0, 4]} wrap>
                      {failures.map(([reason, count]) => (
                        <Tag key={reason}>
                          {collectionFailureLabels[reason] || reason} {count}
                        </Tag>
                      ))}
                    </Space>
                    {bySource.map(([source, values]) => (
                      <Typography.Text type="secondary" key={source} style={{ fontSize: 12 }}>
                        {source}：{Object.entries(values).map(([reason, count]) => (
                          `${collectionFailureLabels[reason] || reason} ${count}`
                        )).join("，")}
                      </Typography.Text>
                    ))}
                  </Space>
                ) : (
                  <Tag color="success">无详情失败</Tag>
                );
              },
            },
            {
              title: "报告",
              key: "report",
              fixed: "right",
              width: 90,
              render: (_: unknown, row) =>
                row.report_download_url ? (
                  <Button
                    type="link"
                    size="small"
                    href={apiResourceUrl(row.report_download_url)}
                  >
                    下载
                  </Button>
                ) : (
                  "—"
                ),
            },
          ]}
        />
      </Drawer>
    </Card>
  );
}
