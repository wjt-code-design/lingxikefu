import { useQuery } from '@tanstack/react-query';
import { Card, Descriptions, Tag, Typography } from 'antd';
import { getRoles } from '@/api/admin';
import { ROUTE_META } from '@/routes.config';
import type { RoleDef } from '@/contracts/api';
import './RolesPage.css';

const { Title, Paragraph, Text } = Typography;

/** 数据范围元信息（与后端 roles scope 对齐） */
const SCOPE_META: Record<string, { text: string; desc: string; color: string }> = {
  all: { text: '全租户', desc: '查看全租户数据与全部管理功能', color: 'red' },
  agent_own: { text: '客服经手', desc: '仅自己经手的会话 / 工单 / 客户', color: 'blue' },
  user_self: { text: '仅自己', desc: '仅自己的会话与工单', color: 'default' },
};

const ROLE_META: Record<string, { text: string; color: string }> = {
  admin: { text: '管理员', color: 'red' },
  agent: { text: '客服', color: 'blue' },
  user: { text: '普通用户', color: 'default' },
};

/**
 * 权限管理页（C9 / 方案 v0.2 §6）：只读展示角色权限矩阵。
 * - 范围：菜单级可见性 + Agent 数据范围；按钮级 RBAC 为 P2 后置项。
 * - 数据：GET /admin/roles（仅 admin），角色定义由后端静态真源返回。
 * - 用户角色分配复用「用户管理」页 PUT /admin/users/{id}/role。
 */
export function RolesPage() {
  const { data, isFetching } = useQuery({
    queryKey: ['admin-roles'],
    queryFn: getRoles,
    placeholderData: (prev) => prev,
  });

  const roles = data?.roles ?? [];

  return (
    <div className="roles-page page-atmo">
      <Typography>
        <Title level={3}>权限管理</Title>
        <Paragraph type="secondary">
          菜单级可见性 + Agent 数据范围（按钮级 RBAC 为 P2 后置项）。角色由系统预置，此处展示权限矩阵；
          用户角色分配请在「用户管理」中操作。
        </Paragraph>
      </Typography>
      <div className="roles-grid">
        {roles.map((r: RoleDef) => {
          const scope = SCOPE_META[r.scope] ?? { text: r.scope, desc: '', color: 'default' };
          const role = ROLE_META[r.role] ?? { text: r.role, color: 'default' };
          return (
            <Card
              key={r.role}
              loading={isFetching && roles.length === 0}
              className="role-card"
              title={
                <div className="role-card-head">
                  <Tag color={role.color}>{role.text}</Tag>
                </div>
              }
            >
              <Descriptions column={1} size="small">
                <Descriptions.Item label="数据范围">
                  <Tag color={scope.color}>{scope.text}</Tag>
                  <Text type="secondary" className="scope-desc">
                    {scope.desc}
                  </Text>
                </Descriptions.Item>
              </Descriptions>
              <div className="menus-block">
                <div className="menus-title">可见菜单（{r.menus.length}）</div>
                <div className="menus-tags">
                  {r.menus.map((m) => {
                    const meta = ROUTE_META[m];
                    return (
                      <Tag key={m} className="menu-tag" title={meta ? `${meta.title} · ${m}` : m}>
                        <span className="menu-tag-title">{meta?.title ?? m}</span>
                        {meta && <span className="menu-tag-path">{m}</span>}
                      </Tag>
                    );
                  })}
                </div>
              </div>
            </Card>
          );
        })}
      </div>
    </div>
  );
}

export default RolesPage;
