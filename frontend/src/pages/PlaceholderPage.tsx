import { Alert, Card, Typography } from "antd";

interface Props {
  title: string;
  phase: string;
  description: string;
}

export default function PlaceholderPage({ title, phase, description }: Props) {
  return (
    <Card title={title} className="page-card">
      <Alert
        type="warning"
        showIcon
        message={`功能将在${phase}实现`}
        description={description}
        style={{ marginBottom: 16 }}
      />
      <Typography.Paragraph type="secondary">
        阶段一仅提供导航与布局占位，不提供硬编码的招投标数据或静态演示结果。
      </Typography.Paragraph>
    </Card>
  );
}
