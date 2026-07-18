import { Alert, Card, Typography } from "antd";

interface Props {
  title: string;
  description: string;
}

export default function PlaceholderPage({ title, description }: Props) {
  return (
    <Card title={title} className="page-card">
      <Alert
        type="warning"
        showIcon
        message="功能暂不可用"
        description={description}
        style={{ marginBottom: 16 }}
      />
      <Typography.Paragraph type="secondary">
        此页面不会用硬编码的招投标数据或静态结果代替实时能力。
      </Typography.Paragraph>
    </Card>
  );
}
