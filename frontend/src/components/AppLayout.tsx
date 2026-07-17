import { Layout, Menu, Typography, theme } from "antd";
import {
  DashboardOutlined,
  CloudServerOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  SettingOutlined,
  FileWordOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { Link, Outlet, useLocation } from "react-router-dom";

const { Header, Sider, Content, Footer } = Layout;

const menuItems = [
  { key: "/", icon: <DashboardOutlined />, label: <Link to="/">系统概览</Link> },
  {
    key: "/tasks/new",
    icon: <FileSearchOutlined />,
    label: <Link to="/tasks/new">新建检索</Link>,
  },
  { key: "/tasks", icon: <HistoryOutlined />, label: <Link to="/tasks">任务列表</Link> },
  {
    key: "/announcements",
    icon: <UnorderedListOutlined />,
    label: <Link to="/announcements">采集结果</Link>,
  },
  {
    key: "/sources",
    icon: <CloudServerOutlined />,
    label: <Link to="/sources">数据源</Link>,
  },
  {
    key: "/reports",
    icon: <FileWordOutlined />,
    label: <Link to="/reports">报告中心</Link>,
  },
  { key: "/settings", icon: <SettingOutlined />, label: <Link to="/settings">设置</Link> },
];

export default function AppLayout() {
  const location = useLocation();
  const {
    token: { colorBgContainer },
  } = theme.useToken();

  const selected =
    menuItems.find((item) =>
      item.key === "/"
        ? location.pathname === "/"
        : location.pathname.startsWith(item.key),
    )?.key || "/";

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider breakpoint="lg" collapsedWidth={64} theme="dark">
        <div
          style={{
            height: 64,
            margin: 16,
            color: "#fff",
            fontWeight: 700,
            fontSize: 16,
            display: "flex",
            alignItems: "center",
          }}
        >
          FusionBid
        </div>
        <Menu theme="dark" mode="inline" selectedKeys={[selected]} items={menuItems} />
      </Sider>
      <Layout>
        <Header
          style={{
            background: colorBgContainer,
            padding: "0 24px",
            display: "flex",
            alignItems: "center",
            borderBottom: "1px solid #f0f0f0",
          }}
        >
          <Typography.Title level={4} style={{ margin: 0 }}>
            智标聚合助手
          </Typography.Title>
          <Typography.Text type="secondary" style={{ marginLeft: 16 }}>
            2026 AI 先锋未来人才大赛 · 超聚变企业命题
          </Typography.Text>
        </Header>
        <Content style={{ margin: 24 }}>
          <Outlet />
        </Content>
        <Footer style={{ textAlign: "center" }} className="muted">
          FusionBid · 默认时区 Asia/Shanghai · 阶段一骨架
        </Footer>
      </Layout>
    </Layout>
  );
}
