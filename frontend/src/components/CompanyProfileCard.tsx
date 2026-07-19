import { useEffect } from "react";
import { Alert, Button, Card, Col, Form, Input, Row, Select, Switch, Typography, message } from "antd";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../api/client";

type CompanyProfile = {
  configured: boolean;
  name: string;
  product_capabilities: string[];
  service_regions: string[];
  qualifications: Array<string | Record<string, unknown>>;
  cases: string[];
  delivery_constraints: string[];
  agent_capability?: boolean | null;
  joint_venture_capability?: boolean | null;
  qualification_expiry_warnings?: string[];
};

const tagProps = {
  mode: "tags" as const,
  tokenSeparators: [",", "，", ";", "；"],
  style: { width: "100%" },
};

export default function CompanyProfileCard() {
  const [form] = Form.useForm();
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ["company-profile"],
    queryFn: async () => {
      const { data } = await apiClient.get<CompanyProfile>("/api/company-profile");
      return data;
    },
  });

  const mutation = useMutation({
    mutationFn: async (values: Record<string, unknown>) => {
      const { data } = await apiClient.put("/api/company-profile", values);
      return data;
    },
    onSuccess: (data) => {
      message.success(data.message || "企业画像已保存");
      qc.invalidateQueries({ queryKey: ["company-profile"] });
    },
    onError: () => message.error("企业画像保存失败"),
  });

  useEffect(() => {
    if (!query.data || form.isFieldsTouched()) return;
    form.setFieldsValue({
      ...query.data,
      qualifications: (query.data.qualifications || []).map((value) =>
        typeof value === "string" ? value : String(value.name || ""),
      ),
    });
  }, [query.data, form]);

  return (
    <Card title="企业画像 · 资格逐条匹配" className="page-card" loading={query.isLoading}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={query.data?.configured ? "已配置企业画像" : "企业画像为可选项"}
        description="配置后，公告 AI 分析会把资格条款与产品能力、服务区域、资质、案例及交付限制逐条比对；未配置时仍提供通用分析。这里不保存账号、Key、Cookie 或登录状态。"
      />
      {(query.data?.qualification_expiry_warnings || []).length > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="资质有效期提醒"
          description={(query.data?.qualification_expiry_warnings || []).join("；")}
        />
      )}
      <Form
        form={form}
        layout="vertical"
        initialValues={{ name: "本地企业画像" }}
        onFinish={(values) => mutation.mutate(values)}
      >
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name="name" label="企业/画像名称" rules={[{ required: true }]}>
              <Input placeholder="例如：超聚变服务器解决方案团队" />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="service_regions" label="可服务区域">
              <Select {...tagProps} placeholder="陕西省、全国…" />
            </Form.Item>
          </Col>
        </Row>
        <Form.Item name="product_capabilities" label="产品与技术能力">
          <Select {...tagProps} placeholder="服务器、数据库集群、安装部署、维保…" />
        </Form.Item>
        <Form.Item name="qualifications" label="资质及有效期（建议把有效期写在同一项）">
          <Select {...tagProps} placeholder="营业执照（有效至…）、ISO 9001（有效至…）…" />
        </Form.Item>
        <Form.Item name="cases" label="可引用案例/业绩">
          <Select {...tagProps} placeholder="项目名称、客户、验收年份…" />
        </Form.Item>
        <Form.Item name="delivery_constraints" label="交付限制与边界">
          <Select {...tagProps} placeholder="不承接驻场、交付周期不少于30天…" />
        </Form.Item>
        <Row gutter={16}>
          <Col xs={24} md={12}>
            <Form.Item name="agent_capability" label="具备代理商投标能力" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Col>
          <Col xs={24} md={12}>
            <Form.Item name="joint_venture_capability" label="具备联合体组织能力" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Col>
        </Row>
        <Button type="primary" htmlType="submit" loading={mutation.isPending}>
          保存企业画像
        </Button>
        <Typography.Text type="secondary" style={{ marginLeft: 12 }}>
          保存后请在公告详情中点击“重新分析”。
        </Typography.Text>
      </Form>
    </Card>
  );
}
