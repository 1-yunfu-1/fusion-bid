import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  Row,
  Select,
  Space,
  Switch,
  Tag,
  TimePicker,
  Typography,
  message,
} from "antd";
import dayjs, { type Dayjs } from "dayjs";
import { useMutation } from "@tanstack/react-query";
import { confirmParse, parseQuery } from "../api/parse";
import type { ParsedIntent, ParseResponse, ValidationIssue } from "../types/intent";

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
    execute_immediately: intent.execute_immediately,
  };
}

function formToIntent(values: Record<string, unknown>): ParsedIntent {
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
      schedule_type: (values.schedule_type as ParsedIntent["schedule"]["schedule_type"]) || null,
      execute_date: execDate ? execDate.format("YYYY-MM-DD") : null,
      execute_time: execTime ? execTime.format("HH:mm") : null,
      timezone: "Asia/Shanghai",
    },
    execute_immediately: Boolean(values.execute_immediately),
  };
}

export default function NewTaskPage() {
  const [query, setQuery] = useState(
    "最近1个月的安徽省区域内的服务器招标信息都有哪些",
  );
  const [parseResult, setParseResult] = useState<ParseResponse | null>(null);
  const [form] = Form.useForm();

  const parseMutation = useMutation({
    mutationFn: () => parseQuery({ query, prefer_llm: true }),
    onSuccess: (data) => {
      setParseResult(data);
      form.setFieldsValue(intentToForm(data.intent));
      if (data.needs_user_input) {
        message.warning("解析结果需要人工确认或补充");
      } else {
        message.success(`解析完成（${data.parser_used}）`);
      }
    },
    onError: (err: Error) => message.error(err.message || "解析失败"),
  });

  const confirmMutation = useMutation({
    mutationFn: async () => {
      const values = await form.validateFields();
      const intent = formToIntent(values);
      return confirmParse({ intent, force: false });
    },
    onSuccess: (data) => {
      message.success(`任务已创建：${data.task_id.slice(0, 8)}…（${data.status}）`);
    },
    onError: (err: unknown) => {
      const anyErr = err as { response?: { data?: { detail?: unknown } }; message?: string };
      const detail = anyErr.response?.data?.detail;
      if (detail && typeof detail === "object" && detail !== null && "message" in detail) {
        const d = detail as { message: string; issues?: ValidationIssue[] };
        message.error(d.message);
        if (d.issues) {
          setParseResult((prev) =>
            prev
              ? { ...prev, issues: d.issues || prev.issues, needs_user_input: true }
              : prev,
          );
        }
      } else {
        message.error(anyErr.message || "确认失败");
      }
    },
  });

  const errorIssues = useMemo(
    () => parseResult?.issues.filter((i) => i.severity === "error") || [],
    [parseResult],
  );

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="自然语言意图解析"
        description="优先调用兼容 API 大模型，失败则尝试本地 Ollama，再降级规则解析。请在下方确认/修改后再创建任务。采集与报告将在后续阶段执行。"
      />

      <Card title="1. 输入查询" className="page-card">
        <TextArea
          rows={4}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="例如：最近3个月的上海区域内的充电桩招标信息都有哪些，请汇总后每天9:00发送给我"
        />
        <Space style={{ marginTop: 12 }}>
          <Button
            type="primary"
            loading={parseMutation.isPending}
            onClick={() => parseMutation.mutate()}
          >
            解析意图
          </Button>
          <Typography.Text type="secondary">
            将按 设置页 中的通道优先级调用模型
          </Typography.Text>
        </Space>
      </Card>

      {parseResult && (
        <>
          <Card title="2. 解析诊断" className="page-card">
            <Space wrap>
              <Tag color="blue">通道: {parseResult.parser_used}</Tag>
              <Tag color={parseResult.llm_success ? "success" : "default"}>
                LLM: {parseResult.llm_success ? "成功" : parseResult.llm_attempted ? "失败/降级" : "未尝试"}
              </Tag>
              {parseResult.llm_error && <Tag color="orange">LLM错误已隐藏密钥</Tag>}
            </Space>
            {parseResult.warnings.map((w) => (
              <Alert key={w} type="warning" showIcon message={w} style={{ marginTop: 8 }} />
            ))}
            {errorIssues.map((i) => (
              <Alert
                key={i.code + i.message}
                type="error"
                showIcon
                message={i.message}
                style={{ marginTop: 8 }}
              />
            ))}
            {parseResult.suggestions.length > 0 && (
              <ul style={{ marginTop: 12 }}>
                {parseResult.suggestions.map((s) => (
                  <li key={s}>{s}</li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="3. 确认 / 修改意图" className="page-card">
            <Form form={form} layout="vertical">
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
                <Col xs={24} md={8}>
                  <Form.Item name="execute_immediately" label="立即执行" valuePropName="checked">
                    <Switch />
                  </Form.Item>
                </Col>
              </Row>
              <Button
                type="primary"
                loading={confirmMutation.isPending}
                onClick={() => confirmMutation.mutate()}
              >
                确认并创建任务
              </Button>
            </Form>
          </Card>
        </>
      )}
    </Space>
  );
}
