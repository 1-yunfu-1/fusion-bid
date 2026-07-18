import { useState } from "react";
import { Button, Drawer, Grid, Layout, Menu, Typography, theme } from "antd";
import {
  CloudServerOutlined,
  DashboardOutlined,
  FileSearchOutlined,
  FileWordOutlined,
  HistoryOutlined,
  MenuOutlined,
  SettingOutlined,
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
  const [navOpen, setNavOpen] = useState(false);
  const location = useLocation();
  const screens = Grid.useBreakpoint();
  const isDesktop = Boolean(screens.lg);
  const {
    token: { colorBgContainer },
  } = theme.useToken();

  const selected =
    menuItems.find((item) =>
      item.key === "/"
        ? location.pathname === "/"
        : location.pathname.startsWith(item.key),
    )?.key || "/";

  const navigation = (
    <>
      <div className="app-logo">FusionBid</div>
      <Menu
        theme="dark"
        mode="inline"
        selectedKeys={[selected]}
        items={menuItems}
        onClick={() => setNavOpen(false)}
      />
    </>
  );

  return (
    <Layout style={{ minHeight: "100vh" }}>
      {isDesktop && (
        <Sider width={220} theme="dark">
          {navigation}
        </Sider>
      )}
      <Drawer
        title={null}
        placement="left"
        open={!isDesktop && navOpen}
        onClose={() => setNavOpen(false)}
        width={240}
        closable={false}
        styles={{ body: { padding: 0, background: "#001529" } }}
      >
        {navigation}
      </Drawer>
      <Layout className="app-main-layout">
        <Header className="app-header" style={{ background: colorBgContainer }}>
          {!isDesktop && (
            <Button
              type="text"
              icon={<MenuOutlined />}
              aria-label="打开导航菜单"
              className="mobile-menu-button"
              onClick={() => setNavOpen(true)}
            />
          )}
          <Typography.Text strong className="app-title">
            智标聚合助手
          </Typography.Text>
          <Typography.Text type="secondary" className="app-subtitle">
            2026 AI 先锋未来人才大赛 · 超聚变企业命题
          </Typography.Text>
        </Header>
        <Content className="app-content">
          <Outlet />
        </Content>
        <Footer className="muted app-footer">
          FusionBid · Asia/Shanghai · 数据来源与模式可追溯
        </Footer>
      </Layout>
    </Layout>
  );
}
