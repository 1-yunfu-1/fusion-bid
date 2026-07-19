import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  DatePicker,
  Descriptions,
  Form,
  Input,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Steps,
  Switch,
  Tag,
  TimePicker,
  Typography,
  message,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { apiResourceUrl } from "../api/client";
import { confirmParse, parseQuery } from "../api/parse";
import { executeTask } from "../api/tasks";
import type { ParsedIntent, ParseResponse, ValidationIssue } from "../types/intent";
import type { TaskExecutionResponse } from "../types/task";
import { formatDateTime } from "../utils/format";

const { TextArea } = Input;

function intentToForm(intent: ParsedIntent) {
  return {
    original_query: intent.original_query,
    keywords: intent.keywords,
    exclude_keywords: intent.exclude_keywords,
    regions: intent.regions,
    start_date: intent.date_range.start_date ? dayjs(intent.date_range.start_date) : null,
    end_date: intent.date_range.end_date ? dayjs(intent.date_range.end_date) : null,
    original_expression: intent.date_range.original_expression,
    schedule_enabled: intent.schedule.enabled,
    schedule_type: intent.schedule.schedule_type,
    execute_date: intent.schedule.execute_date ? dayjs(intent.schedule.execute_date) : null,
    execute_time: intent.schedule.execute_time
      ? dayjs(intent.schedule.execute_time, "HH:mm")
      : null,
    // 比赛演示默认闭环：定时任务也先执行首轮，用户仍可手工关闭。
    execute_immediately: true,
  };
}

function formToIntent(values: Record<string, unknown>): ParsedIntent {
  const start = values.start_date as Dayjs | null;
  const end = values.end_date as Dayjs | null;
  const execDate = values.execute_date as Dayjs | null;
  const execTime = values.execute_time as Dayjs | null;
  const scheduleEnabled = Boolean(values.schedule_enabled);
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
      enabled: scheduleEnabled,
      schedule_type: scheduleEnabled
        ? ((values.schedule_type as ParsedIntent["schedule"]["schedule_type"]) || null)
        : null,
      execute_date: scheduleEnabled && execDate ? execDate.format("YYYY-MM-DD") : null,
      execute_time: scheduleEnabled && execTime ? execTime.format("HH:mm") : null,
      timezone: "Asia/Shanghai",
    },
    execute_immediately: scheduleEnabled ? Boolean(values.execute_immediately) : true,
  };
}

function getErrorMessage(error: unknown, fallback: string): string {
  const err = error as {
    response?: { data?: { detail?: string | { message?: string } } };
    message?: string;
  };
  const detail = err.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && detail.message) return detail.message;
  return err.message || fallback;
}

const executionStatus: Record<string, { label: string; color: string; alert: "success" | "warning" | "error" }> = {
  success: { label: "成功", color: "success", alert: "success" },
  partial: { label: "部分成功", color: "warning", alert: "warning" },
  failed: { label: "失败", color: "error", alert: "error" },
};

const taskStatus: Record<string, { label: string; color: string }> = {
  confirmed: { label: "待执行", color: "blue" },
  scheduled: { label: "已计划", color: "purple" },
  running: { label: "执行中", color: "processing" },
  done: { label: "已完成", color: "success" },
  failed: { label: "执行失败", color: "error" },
  paused: { label: "已暂停", color: "default" },
  expired: { label: "已过期", color: "warning" },
};

export default function NewTaskPage() {
  const qc = useQueryClient();
  const [query, setQuery] = useState(
    "最近1个月的安徽省区域内的服务器招标信息都有哪些",
  );
  const [parseResult, setParseResult] = useState<ParseResponse | null>(null);
  const [createdTaskId, setCreatedTaskId] = useState<string | null>(null);
  const [executionResult, setExecutionResult] = useState<TaskExecutionResponse | null>(null);
  const [executionError, setExecutionError] = useState<string | null>(null);
  const [scheduledWithoutInitial, setScheduledWithoutInitial] = useState(false);
  const [form] = Form.useForm();
  const scheduleEnabled = Form.useWatch("schedule_enabled", form);
  const scheduleType = Form.useWatch("schedule_type", form);
  const executeImmediately = Form.useWatch("execute_immediately", form);
  const confirmationRef = useRef<HTMLDivElement>(null);
  const executionRef = useRef<HTMLDivElement>(null);
  const resultRef = useRef<HTMLDivElement>(null);

  const resetOutcome = () => {
    setCreatedTaskId(null);
    setExecutionResult(null);
    setExecutionError(null);
    setScheduledWithoutInitial(false);
  };

  const executeMutation = useMutation({
    mutationFn: ({ taskId, trigger }: { taskId: string; trigger: "initial" | "manual" }) =>
      executeTask(taskId, trigger),
    onSuccess: (data) => {
      setExecutionResult(data);
      setExecutionError(null);
      qc.invalidateQueries({ queryKey: ["tasks"] });
      qc.invalidateQueries({ queryKey: ["announcements"] });
      qc.invalidateQueries({ queryKey: ["reports"] });
      if (data.status === "success") message.success("首轮检索与报告已完成");
      else if (data.status === "partial") message.warning("首轮检索部分完成，请查看失败来源");
      else message.error("首轮检索失败，任务已保留，可直接重试");
    },
    onError: (error: unknown) => {
      setExecutionError(getErrorMessage(error, "执行失败，任务已保留，可稍后重试"));
    },
  });

  const parseMutation = useMutation({
    mutationFn: () => parseQuery({ query, prefer_llm: true }),
    onSuccess: (data) => {
      resetOutcome();
      setParseResult(data);
      form.setFieldsValue(intentToForm(data.intent));
      if (data.needs_user_input) message.warning("请补充或修正标红内容");
      else message.success("解析完成，请确认检索条件");
    },
    onError: (error: unknown) => message.error(getErrorMessage(error, "解析失败")),
  });

  const confirmMutation = useMutation({
    mutationFn: async () => {
      const values = await form.validateFields();
      return confirmParse({ intent: formToIntent(values), force: false });
    },
    onSuccess: (data) => {
      setCreatedTaskId(data.task_id);
      setExecutionResult(null);
      setExecutionError(null);
      if (data.intent.execute_immediately) {
        executeMutation.mutate({ taskId: data.task_id, trigger: "initial" });
      } else {
        setScheduledWithoutInitial(true);
        qc.invalidateQueries({ queryKey: ["tasks"] });
        message.success("定时任务已创建，将按计划执行");
      }
    },
    onError: (error: unknown) => {
      const anyErr = error as {
        response?: { data?: { detail?: unknown } };
        message?: string;
      };
      const detail = anyErr.response?.data?.detail;
      if (detail && typeof detail === "object" && "message" in detail) {
        const parsed = detail as { message: string; issues?: ValidationIssue[] };
        message.error(parsed.message);
        if (parsed.issues) {
          setParseResult((previous) =>
            previous
              ? { ...previous, issues: parsed.issues || previous.issues, needs_user_input: true }
              : previous,
          );
        }
      } else {
        message.error(anyErr.message || "确认失败");
      }
    },
  });

  useEffect(() => {
    if (parseResult && !createdTaskId) confirmationRef.current?.focus();
  }, [parseResult, createdTaskId]);

  useEffect(() => {
    if (executeMutation.isPending) executionRef.current?.focus();
    if (executionResult || executionError || scheduledWithoutInitial) resultRef.current?.focus();
  }, [executeMutation.isPending, executionResult, executionError, scheduledWithoutInitial]);

  const errorIssues = useMemo(
    () => parseResult?.issues.filter((issue) => issue.severity === "error") || [],
    [parseResult],
  );
  const busy = parseMutation.isPending || confirmMutation.isPending || executeMutation.isPending;
  const currentStep = executionResult || executionError || scheduledWithoutInitial
    ? 4
    : executeMutation.isPending
      ? 3
      : parseResult
        ? 2
        : parseMutation.isPending
          ? 1
          : 0;
  const currentStatus = executionError || executionResult?.status === "failed"
    ? "error"
    : executionResult || scheduledWithoutInitial
      ? "finish"
      : "process";

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Typography.Title level={1} style={{ marginBottom: 4 }}>
          新建检索
        </Typography.Title>
        <Typography.Text type="secondary">
          输入需求、确认解析内容后，系统会立即执行首轮检索并生成 Word 报告。
        </Typography.Text>
      </div>

      <Card className="page-card compact-card">
        <Steps
          current={currentStep}
          status={currentStatus}
          responsive
          items={[
            { title: "输入" },
            { title: "解析" },
            { title: "确认" },
            { title: "执行" },
            { title: "完成" },
          ]}
        />
      </Card>

      <Card title="1. 输入查询" className="page-card">
        <TextArea
          rows={4}
          value={query}
          disabled={busy}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="例如：最近3个月的上海区域内的充电桩招标信息都有哪些，请每天9:00汇总"
          aria-label="招投标查询需求"
        />
        <Space style={{ marginTop: 12 }} wrap>
          <Button
            type="primary"
            loading={parseMutation.isPending}
            disabled={!query.trim() || confirmMutation.isPending || executeMutation.isPending}
            onClick={() => parseMutation.mutate()}
          >
            解析意图
          </Button>
          <Typography.Text type="secondary">
            模型不可用时会自动采用规则解析，仍可继续确认和检索。
          </Typography.Text>
        </Space>
      </Card>

      {parseResult && (
        <div ref={confirmationRef} tabIndex={-1} className="focus-target">
          <Card title="2. 确认检索条件" className="page-card">
            <Alert
              type={errorIssues.length ? "error" : "success"}
              showIcon
              message={errorIssues.length ? "还有内容需要修正" : "解析完成，可以确认执行"}
              description={
                errorIssues.length
                  ? errorIssues.map((issue) => issue.message).join("；")
                  : "请重点检查关键词、区域、日期范围和定时设置。"
              }
              style={{ marginBottom: 16 }}
            />

            <Form form={form} layout="vertical" disabled={confirmMutation.isPending || executeMutation.isPending}>
              <Form.Item name="original_query" label="原始问题" rules={[{ required: true }]}>
                <TextArea rows={2} />
              </Form.Item>
              <Row gutter={16}>
                <Col xs={24} md={12}>
                  <Form.Item name="keywords" label="关键词" rules={[{ required: true, type: "array", min: 1 }]}>
                    <Select mode="tags" placeholder="输入后回车" tokenSeparators={[",", "，"]} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={12}>
                  <Form.Item name="regions" label="区域" rules={[{ required: true, type: "array", min: 1 }]}>
                    <Select mode="tags" placeholder="如 安徽省、上海市" tokenSeparators={[",", "，"]} />
                  </Form.Item>
                </Col>
              </Row>
              <Form.Item name="exclude_keywords" label="排除词">
                <Select mode="tags" tokenSeparators={[",", "，"]} />
              </Form.Item>
              <Row gutter={16}>
                <Col xs={24} md={8}>
                  <Form.Item name="start_date" label="开始日期" rules={[{ required: true }]}>
                    <DatePicker style={{ width: "100%" }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name="end_date" label="结束日期" rules={[{ required: true }]}>
                    <DatePicker style={{ width: "100%" }} />
                  </Form.Item>
                </Col>
                <Col xs={24} md={8}>
                  <Form.Item name="original_expression" label="时间原表达">
                    <Input />
                  </Form.Item>
                </Col>
              </Row>
              <Row gutter={16}>
                <Col xs={24} md={8}>
                  <Form.Item name="schedule_enabled" label="启用定时增量" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </Col>
                {scheduleEnabled && (
                  <>
                    <Col xs={24} md={8}>
                      <Form.Item name="schedule_type" label="频率" rules={[{ required: true, message: "请选择频率" }]}>
                        <Select
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
                      <Form.Item name="execute_time" label="执行时间" rules={[{ required: true, message: "请选择执行时间" }]}>
                        <TimePicker format="HH:mm" style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                  </>
                )}
              </Row>
              {scheduleEnabled && (
                <Row gutter={16}>
                  {scheduleType === "once" && (
                    <Col xs={24} md={8}>
                      <Form.Item name="execute_date" label="单次执行日期" rules={[{ required: true, message: "请选择执行日期" }]}>
                        <DatePicker style={{ width: "100%" }} />
                      </Form.Item>
                    </Col>
                  )}
                  <Col xs={24} md={12}>
                    <Form.Item
                      name="execute_immediately"
                      label="创建后立即执行首轮"
                      valuePropName="checked"
                      extra="默认开启；关闭后只创建定时任务，不执行当前首轮。"
                    >
                      <Switch />
                    </Form.Item>
                  </Col>
                </Row>
              )}

              <Space wrap>
                <Button
                  type="primary"
                  loading={confirmMutation.isPending || executeMutation.isPending}
                  disabled={Boolean(createdTaskId) || errorIssues.length > 0}
                  onClick={() => confirmMutation.mutate()}
                >
                  {scheduleEnabled && !executeImmediately
                    ? "确认并创建定时任务"
                    : "确认并立即检索"}
                </Button>
                {createdTaskId && <Tag color="blue">任务 {createdTaskId.slice(0, 8)}… 已创建</Tag>}
              </Space>
            </Form>

            <Collapse
              ghost
              style={{ marginTop: 16 }}
              items={[
                {
                  key: "parse-details",
                  label: "解析详情",
                  children: (
                    <Space direction="vertical" size="small">
                      <Space wrap>
                        <Tag color="blue">通道：{parseResult.parser_used}</Tag>
                        <Tag color={parseResult.llm_success ? "success" : "default"}>
                          LLM：{parseResult.llm_success ? "成功" : parseResult.llm_attempted ? "已降级" : "未尝试"}
                        </Tag>
                      </Space>
                      {parseResult.llm_error && (
                        <Typography.Text type="secondary">{parseResult.llm_error}</Typography.Text>
                      )}
                      {parseResult.warnings.map((warning) => (
                        <Alert key={warning} type="warning" showIcon message={warning} />
                      ))}
                      {parseResult.suggestions.length > 0 && (
                        <ul style={{ margin: 0, paddingLeft: 20 }}>
                          {parseResult.suggestions.map((suggestion) => <li key={suggestion}>{suggestion}</li>)}
                        </ul>
                      )}
                    </Space>
                  ),
                },
              ]}
            />
          </Card>
        </div>
      )}

      {executeMutation.isPending && (
        <div ref={executionRef} tabIndex={-1} className="focus-target" aria-live="polite">
          <Card title="3. 正在执行首轮检索" className="page-card">
            <Space>
              <Spin />
              <Typography.Text>
                正在连接已启用数据源、清洗去重并生成报告。耗时取决于网站响应，请勿重复提交。
              </Typography.Text>
            </Space>
          </Card>
        </div>
      )}

      {(executionResult || executionError || scheduledWithoutInitial) && (
        <div ref={resultRef} tabIndex={-1} className="focus-target" aria-live="polite">
          <Card title="4. 执行结果" className="page-card">
            {scheduledWithoutInitial && (
              <Alert
                type="success"
                showIcon
                message="定时任务已创建"
                description="当前未执行首轮；系统会在设定时间执行增量检索。"
              />
            )}
            {executionError && (
              <Alert
                type="error"
                showIcon
                message="首轮执行请求失败，任务已保留"
                description={executionError}
              />
            )}
            {executionResult && (() => {
              const meta = executionStatus[executionResult.status] || executionStatus.failed;
              const taskMeta = taskStatus[executionResult.task_status] || {
                label: executionResult.task_status,
                color: "default",
              };
              return (
                <Space direction="vertical" size="middle" style={{ width: "100%" }}>
                  <Alert
                    type={meta.alert}
                    showIcon
                    message={`首轮检索${meta.label}`}
                    description={executionResult.error_message || executionResult.message}
                  />
                  <Row gutter={[16, 16]}>
                    <Col xs={12} md={6}><Statistic title="原始结果" value={executionResult.raw_result_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="本次入库" value={executionResult.saved_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="新增交付" value={executionResult.incremental_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="内容更新" value={executionResult.update_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="已核验详情" value={executionResult.detail_success_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="仅元数据" value={executionResult.detail_metadata_only_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="真实采集失败" value={executionResult.detail_failed_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="站点阻断未尝试" value={executionResult.detail_not_attempted_count} /></Col>
                    <Col xs={12} md={6}><Statistic title="复用历史完整正文" value={executionResult.cached_full_reused_count || 0} /></Col>
                  </Row>
                  <Descriptions size="small" column={{ xs: 1, md: 2 }} bordered>
                    <Descriptions.Item label="任务状态">
                      <Tag color={taskMeta.color}>{taskMeta.label}</Tag>
                    </Descriptions.Item>
                    <Descriptions.Item label="下次运行">{formatDateTime(executionResult.next_run_at || undefined)}</Descriptions.Item>
                    <Descriptions.Item label="成功来源">
                      {executionResult.sources_succeeded.length
                        ? executionResult.sources_succeeded.map((source) => <Tag color="success" key={source}>{source}</Tag>)
                        : "无"}
                    </Descriptions.Item>
                    <Descriptions.Item label="去重数量">{executionResult.duplicate_count}</Descriptions.Item>
                    <Descriptions.Item label="报告范围">
                      {executionResult.report_mode === "full_snapshot" ? "未去重完整快照" : "增量交付"}
                    </Descriptions.Item>
                  </Descriptions>
                  {Object.keys(executionResult.failure_breakdown || {}).length > 0 && (
                    <Alert
                      type="warning"
                      showIcon
                      message="详情采集质量分组"
                      description={Object.entries(executionResult.failure_breakdown).map(([reason, count]) => {
                        const labels: Record<string, string> = {
                          not_attempted: "未尝试",
                          site_blocked: "站点阻断",
                          browser_failure: "浏览器失败",
                          pdf_incomplete: "PDF 不完整",
                          identity_conflict: "身份冲突",
                          extraction_failure: "抽取失败（已规则降级）",
                          html_parse_failure: "HTML 解析失败",
                          html_content_empty: "详情正文为空",
                          http_detail_failure: "HTTP 详情失败",
                          outer_detail_unavailable: "公告外层页暂不可用",
                          official_content_unavailable: "官方正文暂停访问",
                          metadata_only_other: "其他仅元数据",
                        };
                        return <Tag key={reason}>{labels[reason] || reason} {count}</Tag>;
                      })}
                    />
                  )}
                  {Object.keys(executionResult.failure_breakdown_by_source || {}).length > 0 && (
                    <Alert
                      type="info"
                      showIcon
                      message="按数据源定位失败"
                      description={Object.entries(executionResult.failure_breakdown_by_source).map(([source, failures]) => (
                        <div key={source}>
                          <Typography.Text strong>{source}：</Typography.Text>
                          {Object.entries(failures).map(([reason, count]) => (
                            <Tag key={`${source}-${reason}`}>{reason} {count}</Tag>
                          ))}
                        </div>
                      ))}
                    />
                  )}
                  {executionResult.analysis_preview?.portfolio_summary && (
                    <Alert
                      type="info"
                      showIcon
                      message="投标决策分析（规则优先）"
                      description={
                        <Space direction="vertical" size={2}>
                          <span>{executionResult.analysis_preview.portfolio_summary}</span>
                          {Object.entries(executionResult.analysis_preview.priority_counts || {}).map(([priority, count]) => (
                            <Tag key={priority} color={priority === "高" ? "red" : priority === "中" ? "gold" : "blue"}>
                              {priority}优先级 {count} 个
                            </Tag>
                          ))}
                          <Typography.Text type="secondary">
                            {executionResult.analysis_provider === "rules"
                              ? "基于公告原文字段生成；未调用或未采纳模型补充。"
                              : "模型补充内容已通过公告字段证据校验。"}
                          </Typography.Text>
                        </Space>
                      }
                    />
                  )}
                  {Object.keys(executionResult.sources_failed).length > 0 && (
                    <Alert
                      type="warning"
                      showIcon
                      message="以下来源未成功，其他来源结果仍已保留"
                      description={(
                        <ul style={{ margin: 0, paddingLeft: 20 }}>
                          {Object.entries(executionResult.sources_failed).map(([source, reason]) => (
                            <li key={source}><strong>{source}</strong>：{reason}</li>
                          ))}
                        </ul>
                      )}
                    />
                  )}
                  {!executionResult.report_download_url && executionResult.status !== "failed" && (
                    <Alert type="warning" showIcon message="本次没有可下载报告，请查看错误说明后重试" />
                  )}
                </Space>
              );
            })()}
            <Space wrap style={{ marginTop: 16 }}>
              {executionResult?.report_download_url && (
                <Button type="primary" href={apiResourceUrl(executionResult.report_download_url)}>
                  下载 Word 报告
                </Button>
              )}
              {createdTaskId && (
                executionError ||
                (executionResult && (
                  executionResult.status !== "success" || !executionResult.report_download_url
                ))
              ) && (
                <Button
                  loading={executeMutation.isPending}
                  onClick={() => executeMutation.mutate({ taskId: createdTaskId, trigger: "manual" })}
                >
                  {executionResult?.status === "partial" ? "重试未完成项" : "重试执行"}
                </Button>
              )}
              <Button href="/announcements">查看采集结果</Button>
              <Button href="/tasks">查看任务</Button>
              <Button
                onClick={() => {
                  setParseResult(null);
                  resetOutcome();
                  form.resetFields();
                  window.scrollTo({ top: 0, behavior: "smooth" });
                }}
              >
                新建另一个检索
              </Button>
            </Space>
          </Card>
        </div>
      )}
    </Space>
  );
}
