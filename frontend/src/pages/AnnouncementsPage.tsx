import { useState } from "react";
import {
  ExportOutlined,
  ReloadOutlined,
  UploadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Grid,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import type { UploadFile } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";
import { fetchHealth } from "../api/health";
import { listTasks } from "../api/tasks";
import { formatDateTime } from "../utils/format";

type AnnouncementRow = {
  id: string;
  title: string;
  source_name: string;
  source_url: string;
  detail_url?: string;
  data_mode: string;
  detail_status: string;
  content_format?: string | null;
  extraction_version?: string;
  region?: string;
  summary?: string;
  publish_time?: string;
  attachment_links?: string[];
  related_sources?: Array<Record<string, unknown>>;
  project_code?: string;
  lifecycle_stage?: string;
  procurement_method?: string;
  detail_attempt_state?: "not_attempted" | "attempted" | "blocked" | string;
  failure_reason?: string | null;
  failure_stage?: string | null;
  terminal_failure?: boolean;
  retryable?: boolean;
  viewer_error_code?: string | null;
  viewer_error_message?: string | null;
  fallback_attempted?: boolean;
  fallback_result?: string | null;
  cooldown_until?: string | null;
  time_to_failure_ms?: number;
  source_metadata?: Record<string, unknown>;
};

type Evidence = {
  evidence_id?: string | null;
  source_label?: string | null;
  page?: number | null;
  quote?: string | null;
  method?: string;
  status?: string;
};

type Analysis = {
  lifecycle_stage?: string;
  is_opportunity?: boolean;
  decision?: string;
  priority?: string;
  priority_reasons?: string[];
  deadline_urgency?: string;
  deadline_note?: string;
  gaps?: string[];
  recommended_actions?: string[];
  evidence_ids?: string[];
  technical_business_risks?: string[];
  missing_materials?: string[];
  timeline?: Array<{ milestone?: string; time?: string; evidence_id?: string }>;
  qualification_matrix?: Array<{
    clause_id?: string;
    requirement?: string;
    status?: string;
    profile_basis?: string;
  }>;
};

type AnnouncementDetail = AnnouncementRow & {
  clean_content: string;
  fields: Record<string, unknown>;
  field_evidence: Record<string, Evidence>;
  completeness: { label?: string; assessable?: boolean };
  data_quality: Record<string, unknown>;
  analysis_data: Analysis;
  corrections: Array<{
    id: string;
    field_name: string;
    previous_value: unknown;
    corrected_value: unknown;
    reason: string;
    corrected_at: string;
  }>;
  feedback?: Array<{
    id: string;
    field_name?: string | null;
    verdict: "correct" | "incorrect";
    reason?: string | null;
    created_at: string;
  }>;
  review_status?: "verified" | "needs_review" | "unreviewed";
  crawl_attempts?: Array<{
    id: string;
    source_name: string;
    stage: string;
    outcome: string;
    failure_code?: string | null;
    duration_ms: number;
    attempted_at: string;
  }>;
};

const detailMeta: Record<string, { label: string; color: string }> = {
  full: { label: "已核验详情", color: "success" },
  metadata_only: { label: "仅列表元数据", color: "default" },
  failed: { label: "详情失败", color: "error" },
  needs_human_verification: { label: "待人工安全验证", color: "warning" },
  unknown: { label: "状态未知", color: "default" },
};

const fieldLabels: Record<string, string> = {
  purchaser: "采购主体",
  tenderer: "招标人",
  agency: "招标/采购代理机构",
  transaction_platform: "交易平台",
  project_code: "项目编号",
  budget: "预算",
  awardee: "中标人/成交供应商",
  award_amount: "中标/成交金额",
  change_summary: "更正/澄清事项",
  termination_reason: "终止/废标原因",
  lifecycle_stage: "公告生命周期",
  procurement_method: "采购方式",
  document_price: "招标文件售价",
  funding_source: "资金来源",
  document_acquisition_start: "文件获取开始",
  document_acquisition_end: "文件获取截止",
  bid_deadline: "投标截止",
  opening_time: "开标时间",
  qualification: "资格要求",
  joint_venture_allowed: "联合体条件",
  agent_allowed: "代理商条件",
  platform_registration_required: "平台注册",
  ca_required: "CA/电子签章",
};

const failureLabels: Record<string, string> = {
  pdf_invalid_or_corrupt: "官方 PDF 无效或损坏",
  invalid_pdf_cooldown: "损坏 PDF 冷却期内已跳过",
  pdf_document_unavailable: "PDF 文档暂未就绪",
  collector_timeout: "采集器等待超时",
  pdf_bytes_timeout: "PDF 字节读取超时",
  incomplete_pdf_pages: "PDF 页面不完整",
  ocr_failure: "扫描页 OCR 未识别正文",
  ocr_timeout: "扫描页 OCR 超时",
  official_content_unavailable: "官方正文暂停或停止提供",
};

function statusTag(status: string) {
  const meta = detailMeta[status] || { label: status, color: "default" };
  return <Tag color={meta.color}>{meta.label}</Tag>;
}

function valueText(value: unknown) {
  if (Array.isArray(value)) return value.join("；") || "—";
  if (value === null || value === undefined || value === "") return "—";
  return String(value);
}

export default function AnnouncementsPage() {
  const qc = useQueryClient();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [sourceName, setSourceName] = useState<string>();
  const [dataMode, setDataMode] = useState<string>();
  const [taskId, setTaskId] = useState<string>();
  const [detailStatus, setDetailStatus] = useState<string>();
  const [lifecycleStage, setLifecycleStage] = useState<string>();
  const [selectedId, setSelectedId] = useState<string>();
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importFiles, setImportFiles] = useState<UploadFile[]>([]);
  const [correctionForm] = Form.useForm();
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: fetchHealth });
  const recrawlCompatible = Boolean(
    healthQuery.data?.capabilities?.includes("managed-public-browser-pool-v2"),
  );
  const importCompatible = Boolean(
    healthQuery.data?.capabilities?.includes("official-document-import-v1"),
  );
  const publicBrowserState = healthQuery.data?.public_browser?.state;

  const sourceQuery = useQuery({
    queryKey: ["sources"],
    queryFn: async () => {
      const { data } = await apiClient.get("/api/sources");
      return data as { items: Array<{ source_name: string; display_name: string }> };
    },
  });
  const taskQuery = useQuery({ queryKey: ["tasks"], queryFn: listTasks });
  const listQuery = useQuery({
    queryKey: ["announcements", sourceName, dataMode, taskId, detailStatus, lifecycleStage],
    queryFn: async () => {
      const { data } = await apiClient.get("/api/announcements", {
        params: {
          source_name: sourceName,
          data_mode: dataMode,
          task_id: taskId,
          detail_status: detailStatus,
          lifecycle_stage: lifecycleStage,
        },
      });
      return data as { items: AnnouncementRow[]; total: number };
    },
  });
  const detailQuery = useQuery({
    queryKey: ["announcement-detail", selectedId],
    queryFn: async () => {
      const { data } = await apiClient.get<AnnouncementDetail>(
        `/api/announcements/${selectedId}`,
      );
      return data;
    },
    enabled: Boolean(selectedId),
  });

  const actionMutation = useMutation({
    mutationFn: async (
      action: "recrawl" | "recrawlInteractive" | "reextract" | "analyze",
    ) => {
      const isRecrawl = action === "recrawl" || action === "recrawlInteractive";
      const endpoint = action === "recrawlInteractive" ? "recrawl" : action;
      const { data } = await apiClient.post(
        `/api/announcements/${selectedId}/${endpoint}`,
        isRecrawl
          ? { interactive_on_verification: action === "recrawlInteractive" }
          : {},
        { timeout: isRecrawl ? 360000 : 300000 },
      );
      return data;
    },
    onSuccess: (data, action) => {
      if ((action === "recrawl" || action === "recrawlInteractive") && data.ok === false) {
        const detail = [data.failure_reason, data.acquisition_mode, data.browser_state]
          .filter(Boolean)
          .join(" / ");
        message.warning(
          `${data.message || "本次未获得已验证详情"}${detail ? `（${detail}）` : ""}`,
        );
      } else {
        message.success(data.message || "操作完成");
      }
      qc.invalidateQueries({ queryKey: ["announcement-detail", selectedId] });
      qc.invalidateQueries({ queryKey: ["announcements"] });
    },
    onError: (error: unknown) => {
      const value = error as { response?: { data?: { detail?: string } }; message?: string };
      message.error(value.response?.data?.detail || value.message || "操作失败");
    },
  });

  const importMutation = useMutation({
    mutationFn: async () => {
      const file = importFiles[0]?.originFileObj;
      if (!file || !selectedId) throw new Error("请选择官方 PDF 或 HTML 文件");
      const body = new FormData();
      body.append("file", file);
      const { data } = await apiClient.post(
        `/api/announcements/${selectedId}/import-detail`,
        body,
        { timeout: 300000 },
      );
      return data;
    },
    onSuccess: (data) => {
      message.success(data.message || "官方文件已导入");
      setImportOpen(false);
      setImportFiles([]);
      qc.invalidateQueries({ queryKey: ["announcement-detail", selectedId] });
      qc.invalidateQueries({ queryKey: ["announcements"] });
    },
    onError: (error: unknown) => {
      const value = error as { response?: { data?: { detail?: string } }; message?: string };
      message.error(value.response?.data?.detail || value.message || "官方文件导入失败");
    },
  });

  const correctionMutation = useMutation({
    mutationFn: async (values: { field_name: string; value: string; reason: string }) => {
      const { data } = await apiClient.patch(`/api/announcements/${selectedId}/fields`, {
        fields: { [values.field_name]: values.value },
        reason: values.reason,
      });
      return data;
    },
    onSuccess: (data) => {
      message.success(data.message || "人工校正已保存");
      setCorrectionOpen(false);
      correctionForm.resetFields();
      qc.invalidateQueries({ queryKey: ["announcement-detail", selectedId] });
      qc.invalidateQueries({ queryKey: ["announcements"] });
    },
    onError: () => message.error("人工校正保存失败"),
  });

  const feedbackMutation = useMutation({
    mutationFn: async (values: { verdict: "correct" | "incorrect"; reason?: string }) => {
      const { data } = await apiClient.post(`/api/announcements/${selectedId}/feedback`, values);
      return data;
    },
    onSuccess: (data) => {
      message.success(data.message || "质量反馈已记录");
      qc.invalidateQueries({ queryKey: ["announcement-detail", selectedId] });
    },
    onError: () => message.error("质量反馈保存失败"),
  });

  const markIncorrect = () => {
    let reason = "";
    Modal.confirm({
      title: "标记这条公告信息有误",
      content: (
        <Input.TextArea
          autoFocus
          placeholder="请简述错误字段或问题，便于后续复核"
          onChange={(event) => { reason = event.target.value; }}
        />
      ),
      okText: "提交反馈",
      cancelText: "取消",
      onOk: () => {
        if (!reason.trim()) {
          message.warning("请填写错误原因");
          return Promise.reject(new Error("reason required"));
        }
        return feedbackMutation.mutateAsync({ verdict: "incorrect", reason: reason.trim() });
      },
    });
  };

  const detail = detailQuery.data;
  const analysis = detail?.analysis_data || {};
  const needsReviewFields = Array.isArray(detail?.data_quality?.needs_review_fields)
    ? (detail.data_quality.needs_review_fields as string[])
    : [];
  const evidenceRows = Object.entries(detail?.field_evidence || {}).map(
    ([field, value]) => ({ field, ...value }),
  );
  const fieldsItems = Object.entries(detail?.fields || {})
    .filter(([key]) => key in fieldLabels)
    .map(([key, value]) => ({
      key,
      label: fieldLabels[key],
      children: valueText(value),
      span: key === "qualification" && !isMobile ? 3 : 1,
    }));

  const detailActions = (
    <div className="announcement-detail-actions">
      <Button
        icon={<ExportOutlined />}
        href={detail?.detail_url || detail?.source_url}
        target="_blank"
        rel="noreferrer"
        disabled={!detail?.detail_url && !detail?.source_url}
      >
        浏览器打开官方页
      </Button>
      <Button
        icon={<UploadOutlined />}
        disabled={!importCompatible}
        onClick={() => setImportOpen(true)}
      >
        导入官方文件
      </Button>
      <Button
        type="primary"
        icon={<ReloadOutlined />}
        disabled={!recrawlCompatible}
        loading={actionMutation.isPending && actionMutation.variables === "recrawl"}
        onClick={() => actionMutation.mutate("recrawl")}
      >
        全自动重新采集
      </Button>
      {detail?.detail_status === "needs_human_verification" ? (
        <Button
          icon={<ReloadOutlined />}
          disabled={!recrawlCompatible}
          loading={
            actionMutation.isPending && actionMutation.variables === "recrawlInteractive"
          }
          onClick={() => actionMutation.mutate("recrawlInteractive")}
        >
          打开专用浏览器完成验证
        </Button>
      ) : null}
      <Button
        disabled={detail?.detail_status !== "full"}
        loading={actionMutation.isPending && actionMutation.variables === "reextract"}
        onClick={() => actionMutation.mutate("reextract")}
      >
        仅重新解析
      </Button>
      <Button
        loading={actionMutation.isPending && actionMutation.variables === "analyze"}
        onClick={() => actionMutation.mutate("analyze")}
      >
        重新分析
      </Button>
      <Button type="primary" onClick={() => setCorrectionOpen(true)}>人工校正</Button>
      <Button
        loading={feedbackMutation.isPending}
        onClick={() => feedbackMutation.mutate({ verdict: "correct" })}
      >信息正确</Button>
      <Button danger loading={feedbackMutation.isPending} onClick={markIncorrect}>信息有误</Button>
    </div>
  );

  return (
    <Card
      title={<Typography.Title level={1} style={{ margin: 0, fontSize: 26 }}>采集结果</Typography.Title>}
      className="page-card"
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="详情正文、字段证据与 AI 决策分层展示"
        description="live=真实抓取；fixture=演示数据。AI 字段必须能回指原文证据与 PDF 页码；详情未获得时完整度显示不可评估。"
      />
      <Space wrap style={{ marginBottom: 16 }} aria-label="采集结果筛选">
        <Select
          aria-label="按来源筛选"
          allowClear
          style={{ minWidth: 190 }}
          placeholder="按来源筛选"
          value={sourceName}
          onChange={setSourceName}
          options={(sourceQuery.data?.items || []).map((source) => ({
            value: source.source_name,
            label: source.display_name,
          }))}
        />
        <Select
          aria-label="按数据模式筛选"
          allowClear
          style={{ minWidth: 150 }}
          placeholder="按数据模式筛选"
          value={dataMode}
          onChange={setDataMode}
          options={[
            { value: "live", label: "live · 实时数据" },
            { value: "fixture", label: "fixture · 演示数据" },
          ]}
        />
        <Select
          aria-label="按详情状态筛选"
          allowClear
          style={{ minWidth: 180 }}
          placeholder="按详情状态筛选"
          value={detailStatus}
          onChange={setDetailStatus}
          options={Object.entries(detailMeta).map(([value, meta]) => ({ value, label: meta.label }))}
        />
        <Select
          aria-label="按公告生命周期筛选"
          allowClear
          style={{ minWidth: 180 }}
          placeholder="按生命周期筛选"
          value={lifecycleStage}
          onChange={setLifecycleStage}
          options={[
            { value: "机会公告", label: "可参与机会" },
            { value: "更正/澄清", label: "更正/澄清" },
            { value: "结果公告", label: "结果公告" },
            { value: "终止/废标", label: "终止/废标" },
            { value: "待复核", label: "待复核" },
          ]}
        />
        <Select
          aria-label="按交付任务筛选"
          allowClear
          showSearch
          optionFilterProp="label"
          style={{ minWidth: 260 }}
          placeholder="按增量交付任务筛选"
          value={taskId}
          onChange={setTaskId}
          options={(taskQuery.data?.items || []).map((task) => ({
            value: task.id,
            label: task.original_query,
          }))}
        />
      </Space>
      <div aria-live="polite">
        <Table
          loading={listQuery.isLoading}
          rowKey="id"
          dataSource={listQuery.data?.items || []}
          pagination={{ total: listQuery.data?.total || 0 }}
          scroll={{ x: 1280 }}
          columns={[
            {
              title: "模式",
              dataIndex: "data_mode",
              width: 100,
              render: (mode: string) => (
                <Tag color={mode === "live" ? "green" : "gold"}>{mode}</Tag>
              ),
            },
            {
              title: "标题",
              dataIndex: "title",
              ellipsis: true,
              render: (title: string, row: AnnouncementRow) => (
                <Button type="link" style={{ padding: 0 }} onClick={() => setSelectedId(row.id)}>
                  {title}
                </Button>
              ),
            },
            { title: "来源", dataIndex: "source_name", width: 100 },
            { title: "生命周期", dataIndex: "lifecycle_stage", width: 120, render: valueText },
            { title: "采购方式", dataIndex: "procurement_method", width: 120, render: valueText },
            {
              title: "详情质量",
              width: 150,
              render: (_: unknown, row: AnnouncementRow) => (
                row.detail_attempt_state === "not_attempted"
                  ? <Tag>未尝试</Tag>
                  : row.detail_attempt_state === "blocked"
                    ? <Tag color="warning">站点阻断</Tag>
                    : statusTag(row.detail_status)
              ),
            },
            { title: "格式", dataIndex: "content_format", width: 100, render: valueText },
            { title: "项目编号", dataIndex: "project_code", width: 190, render: valueText },
            { title: "区域", dataIndex: "region", width: 100, render: valueText },
            {
              title: "发布时间",
              dataIndex: "publish_time",
              width: 160,
              render: (value?: string) => formatDateTime(value),
            },
            {
              title: "操作",
              key: "action",
              width: 160,
              fixed: "right",
              render: (_: unknown, row: AnnouncementRow) => (
                <Space size={0}>
                  <Button type="link" onClick={() => setSelectedId(row.id)}>详情与分析</Button>
                  <Button type="link" href={row.detail_url || row.source_url} target="_blank">
                    官方页
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </div>

      <Drawer
        title={<span className="announcement-detail-title">{detail?.title || "公告详情"}</span>}
        open={Boolean(selectedId)}
        onClose={() => setSelectedId(undefined)}
        width={isMobile ? "100%" : 980}
        loading={detailQuery.isLoading}
        rootClassName="announcement-detail-drawer"
        extra={isMobile ? undefined : detailActions}
      >
        {detail && (
          <>
            {isMobile && detailActions}
            <div aria-live="polite">
              {actionMutation.isPending && actionMutation.variables === "recrawl" ? (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="正在全自动采集官方详情"
                  description="系统正在启动或复用专用 Chrome、加载公告、逐页读取 PDF，并继续完成证据抽取与 AI 分析。无需安装扩展或逐条点击。"
                />
              ) : null}
              {actionMutation.isPending && actionMutation.variables === "recrawlInteractive" ? (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="正在等待专用浏览器完成官方验证"
                  description="只需完成官方页面明确要求的验证码；验证通过后系统会自动继续逐页采集和解析，无需再次点击。"
                />
              ) : null}
              {detail.detail_status === "needs_human_verification" ? (
                <Alert
                  type="warning"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="官方页面要求人工安全验证"
                  description="正常公开页会完全自动处理；仅在官方明确要求验证码时，使用上方按钮打开 FusionBid 专用浏览器完成一次验证。"
                />
              ) : null}
              {detail.detail_attempt_state === "not_attempted" ? (
                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="该详情尚未实际尝试采集"
                  description="这是旧记录或本轮断路后保留的列表元数据，不应计入真实采集失败。"
                />
              ) : null}
              {detail.failure_reason ? (
                <Alert
                  type={detail.failure_reason === "invalid_pdf_cooldown" ? "info" : "warning"}
                  showIcon
                  style={{ marginBottom: 16 }}
                  message={failureLabels[detail.failure_reason] || "最近一次采集诊断"}
                  description={[
                    `${detail.failure_stage || "unknown"} / ${detail.failure_reason}`,
                    detail.viewer_error_message,
                    detail.fallback_attempted
                      ? `官方 JSON 兜底：${detail.fallback_result || "无有效正文"}`
                      : undefined,
                    detail.cooldown_until
                      ? `自动重试时间：${formatDateTime(detail.cooldown_until)}`
                      : undefined,
                    detail.time_to_failure_ms
                      ? `本次耗时：${(detail.time_to_failure_ms / 1000).toFixed(1)} 秒`
                      : undefined,
                  ].filter(Boolean).join("；")}
                />
              ) : null}
              {publicBrowserState === "unavailable" ? (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="专用浏览器不可用"
                  description={healthQuery.data?.public_browser?.last_error || "未找到可用的 Chrome/Edge 或浏览器启动失败。"}
                />
              ) : null}
              {!healthQuery.isLoading && (!recrawlCompatible || !importCompatible) ? (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="详情采集工具已禁用"
                  description="当前后端缺少专用浏览器自动采集或官方文件导入能力，请先使用 FusionBid 启动脚本安全重启。"
                />
              ) : null}
              {needsReviewFields.length > 0 ? (
                <Alert
                  type="error"
                  showIcon
                  style={{ marginBottom: 16 }}
                  message="部分字段提取失败，待复核"
                  description={`正文中存在字段标签，但解析未得到可信值：${needsReviewFields.map((field) => fieldLabels[field] || field).join("、")}`}
                />
              ) : null}
            </div>
            <Tabs
              items={[
              {
                key: "fields",
                label: "结构化字段",
                children: (
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Alert
                      type={detail.detail_status === "full" ? "success" : "warning"}
                      showIcon
                      message={statusTag(detail.detail_status)}
                      description={`内容格式：${detail.content_format || "—"}；抽取版本：${detail.extraction_version || "—"}；完整度：${detail.completeness?.label || "—"}`}
                    />
                    <Descriptions bordered size="small" column={{ xs: 1, sm: 2, lg: 3 }} items={fieldsItems} />
                  </Space>
                ),
              },
              {
                key: "evidence",
                label: `原文证据 ${evidenceRows.length}`,
                children: (
                  <Table
                    rowKey={(row) => `${row.field}-${row.evidence_id}`}
                    dataSource={evidenceRows}
                    pagination={false}
                    scroll={{ x: 760 }}
                    columns={[
                      { title: "字段", dataIndex: "field", width: 140, render: (value) => fieldLabels[value] || value },
                      { title: "证据 ID", dataIndex: "evidence_id", width: 150, render: valueText },
                      { title: "原文标签", dataIndex: "source_label", width: 130, render: valueText },
                      { title: "PDF 页", dataIndex: "page", width: 80, render: valueText },
                      { title: "方式", dataIndex: "method", width: 120, render: valueText },
                      { title: "原文片段", dataIndex: "quote", render: (value) => <Typography.Paragraph copyable ellipsis={{ rows: 4, expandable: true }}>{valueText(value)}</Typography.Paragraph> },
                    ]}
                  />
                ),
              },
              {
                key: "analysis",
                label: "AI 决策分析",
                children: (
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Alert
                      type={analysis.decision === "建议参与" ? "success" : analysis.decision === "不建议参与" ? "error" : "warning"}
                      showIcon
                      message={analysis.is_opportunity === false
                        ? `项目生命周期情报：${analysis.lifecycle_stage || "待复核"}`
                        : `参与建议：${analysis.decision || "信息不足"}`}
                      description={analysis.is_opportunity === false
                        ? `该公告不作为新的投标机会评分；证据：${(analysis.evidence_ids || []).join("、") || "无"}`
                        : `机会优先级：${analysis.priority || "待核验"}；时间紧迫度：${analysis.deadline_urgency || "未知"}；证据：${(analysis.evidence_ids || []).join("、") || "无"}`}
                    />
                    {analysis.priority_reasons?.map((value) => <Alert key={value} type="info" message={value} />)}
                    <Descriptions
                      bordered
                      column={1}
                      items={[
                        { key: "gaps", label: "信息缺口", children: (analysis.gaps || []).join("；") || "无明显缺口" },
                        { key: "risks", label: "技术/商务风险", children: (analysis.technical_business_risks || []).join("；") || "待结合企业画像核验" },
                        { key: "materials", label: "缺失/建议材料", children: (analysis.missing_materials || []).join("；") || "暂无明确材料清单" },
                        { key: "actions", label: "下一步建议", children: (analysis.recommended_actions || []).join("；") || "—" },
                      ]}
                    />
                    <Typography.Title level={5}>时间倒排</Typography.Title>
                    <Table
                      rowKey={(row) => `${row.milestone}-${row.time}`}
                      dataSource={analysis.timeline || []}
                      pagination={false}
                      columns={[
                        { title: "里程碑", dataIndex: "milestone" },
                        { title: "时间", dataIndex: "time", width: 190 },
                        { title: "证据 ID", dataIndex: "evidence_id", width: 160, render: valueText },
                      ]}
                    />
                    <Typography.Title level={5}>资格逐条匹配矩阵</Typography.Title>
                    <Table
                      rowKey={(row) => row.clause_id || row.requirement || "row"}
                      dataSource={analysis.qualification_matrix || []}
                      pagination={false}
                      scroll={{ x: 760 }}
                      columns={[
                        { title: "条款", dataIndex: "clause_id", width: 80 },
                        { title: "资格要求", dataIndex: "requirement" },
                        { title: "匹配状态", dataIndex: "status", width: 150 },
                        { title: "企业画像依据", dataIndex: "profile_basis", width: 260 },
                      ]}
                    />
                  </Space>
                ),
              },
              {
                key: "lifecycle",
                label: "项目生命周期",
                children: (
                  <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                    <Alert
                      type="info"
                      showIcon
                      message={`当前节点：${detail.lifecycle_stage || "待复核"}`}
                      description={`项目编号：${detail.project_code || "原文未明确说明"}。同项目编号的招标、更正、终止和结果公告只建立关联，不互相覆盖。`}
                    />
                    <Table
                      rowKey={(row) => String(row.announcement_id || `${row.source_name}-${row.source_url}-${row.announcement_type}`)}
                      dataSource={detail.related_sources || []}
                      pagination={false}
                      columns={[
                        { title: "关联类型", dataIndex: "relation", width: 120, render: valueText },
                        { title: "公告类型", dataIndex: "announcement_type", width: 160, render: valueText },
                        { title: "来源", dataIndex: "source_name", width: 120, render: valueText },
                        { title: "关联依据", dataIndex: "reason", render: valueText },
                        { title: "官方地址", dataIndex: "source_url", render: (value) => value ? <a href={String(value)} target="_blank" rel="noreferrer">打开</a> : "—" },
                      ]}
                    />
                  </Space>
                ),
              },
              {
                key: "original",
                label: "保存的公告正文",
                children: detail.detail_status === "full" ? (
                  <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: "65vh", overflow: "auto", padding: 16, background: "#f6f8fa" }}>
                    {detail.clean_content}
                  </pre>
                ) : (
                  <Alert type="warning" showIcon message="详情未获取，无法展示或抽取完整正文" />
                ),
              },
              {
                key: "crawl-audit",
                label: `采集审计 ${detail.crawl_attempts?.length || 0}`,
                children: (
                  <Table
                    rowKey="id"
                    dataSource={detail.crawl_attempts || []}
                    pagination={{ pageSize: 10 }}
                    scroll={{ x: 760 }}
                    columns={[
                      { title: "时间", dataIndex: "attempted_at", width: 170, render: formatDateTime },
                      { title: "来源", dataIndex: "source_name", width: 120 },
                      { title: "阶段", dataIndex: "stage", width: 150 },
                      { title: "结果", dataIndex: "outcome", width: 120 },
                      { title: "失败码", dataIndex: "failure_code", render: valueText },
                      { title: "耗时", dataIndex: "duration_ms", width: 100, render: (value) => `${value} ms` },
                    ]}
                  />
                ),
              },
              {
                key: "audit",
                label: `校正审计 ${detail.corrections?.length || 0}`,
                children: (
                  <Table
                    rowKey="id"
                    dataSource={detail.corrections || []}
                    pagination={false}
                    scroll={{ x: 760 }}
                    columns={[
                      { title: "字段", dataIndex: "field_name", width: 140, render: (value) => fieldLabels[value] || value },
                      { title: "原值", dataIndex: "previous_value", render: valueText },
                      { title: "校正值", dataIndex: "corrected_value", render: valueText },
                      { title: "原因", dataIndex: "reason" },
                      { title: "时间", dataIndex: "corrected_at", width: 170, render: formatDateTime },
                    ]}
                  />
                ),
              },
              ]}
            />
          </>
        )}
      </Drawer>

      <Modal
        title="导入官方详情文件"
        open={importOpen}
        onCancel={() => {
          setImportOpen(false);
          setImportFiles([]);
        }}
        onOk={() => importMutation.mutate()}
        okText="导入并分析"
        confirmLoading={importMutation.isPending}
        okButtonProps={{ disabled: importFiles.length === 0 }}
        destroyOnClose
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="文件必须与当前公告匹配"
          description="支持官方 PDF、HTML，最大 20 MB、PDF 最多 100 页。系统校验公告标题或项目编号后再抽取，不保存原始文件。"
        />
        <Upload.Dragger
          accept=".pdf,.html,.htm,application/pdf,text/html"
          maxCount={1}
          fileList={importFiles}
          beforeUpload={() => false}
          onChange={({ fileList }) => setImportFiles(fileList.slice(-1))}
          onRemove={() => {
            setImportFiles([]);
            return true;
          }}
        >
          <p className="ant-upload-drag-icon"><UploadOutlined /></p>
          <p className="ant-upload-text">选择或拖入官方 PDF / HTML</p>
        </Upload.Dragger>
      </Modal>

      <Modal
        title="人工校正公告字段"
        open={correctionOpen}
        onCancel={() => setCorrectionOpen(false)}
        onOk={() => correctionForm.submit()}
        confirmLoading={correctionMutation.isPending}
        destroyOnClose
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="人工校正优先于自动抽取，并会保留原值、原因与时间"
        />
        <Form form={correctionForm} layout="vertical" onFinish={(values) => correctionMutation.mutate(values)}>
          <Form.Item name="field_name" label="字段" rules={[{ required: true }]}>
            <Select options={Object.entries(fieldLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Form.Item name="value" label="校正值" rules={[{ required: true }]}>
            <Input.TextArea rows={4} />
          </Form.Item>
          <Form.Item name="reason" label="校正原因" rules={[{ required: true, min: 2 }]}>
            <Input.TextArea rows={3} placeholder="例如：官方 PDF 第 1 页明确写为……" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
