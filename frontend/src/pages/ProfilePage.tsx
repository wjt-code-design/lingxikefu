import { useQuery } from '@tanstack/react-query';
import { Button, Card, Col, Row, Spin, Tag, Typography } from 'antd';
import { ArrowLeftOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { me as fetchMe } from '@/api/auth';
import { listSessions } from '@/api/sessions';
import { useAuthStore } from '@/store/authStore';
import { QueryErrorState } from '@/components/common/QueryErrorState';

const ROLE_LABEL: Record<string, string> = { user: '普通用户', agent: '客服', admin: '管理员' };

/** 个人中心（T4'）：账号信息 + 额度 + 最近会话（GET /auth/me 已有）。 */
export function ProfilePage() {
  const navigate = useNavigate();
  const role = useAuthStore((s) => s.role);
  // 返回目标：user→对话，agent/admin→工作台（与 WidgetShell 品牌跳转一致）
  const backPath = role === 'admin' ? '/admin/dashboard' : role === 'agent' ? '/agent/dashboard' : '/chat';
  const { data: me, isLoading, isError, refetch } = useQuery({
    queryKey: ['profile-me'],
    queryFn: fetchMe,
  });
  const { data: sessions } = useQuery({
    queryKey: ['profile-sessions'],
    queryFn: () => listSessions({ page: 1, size: 5 }),
    enabled: !!me,
  });

  if (isLoading) return <Spin className="profile-loading" />;
  if (isError || !me) return <QueryErrorState title="个人信息加载失败" onRetry={() => refetch()} />;

  return (
    <div className="profile">
      <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(backPath)} className="profile__back">
        返回{role === 'admin' || role === 'agent' ? '工作台' : '对话'}
      </Button>
      <Card className="profile__card">
        <Typography.Title level={4}>个人中心</Typography.Title>
        <Row gutter={[16, 12]}>
          <Col xs={24} sm={12}>
            <Typography.Text type="secondary">账号</Typography.Text>
            <div className="profile__value">{me?.email || me?.phone || '—'}</div>
          </Col>
          <Col xs={24} sm={12}>
            <Typography.Text type="secondary">角色</Typography.Text>
            <div className="profile__value">
              <Tag color={me?.role === 'admin' ? 'gold' : me?.role === 'agent' ? 'cyan' : 'blue'}>
                {ROLE_LABEL[me?.role ?? 'user']}
              </Tag>
            </div>
          </Col>
          <Col xs={24} sm={12}>
            <Typography.Text type="secondary">剩余对话额度</Typography.Text>
            <div className="profile__value">{me?.quota_left ?? 0} 次</div>
          </Col>
        </Row>
      </Card>

      <Card className="profile__card" title="最近会话">
        {sessions?.items.length ? (
          sessions.items.map((s) => (
            <div key={s.id} className="profile__session">
              <span>{s.title || '新对话'}</span>
              <span className="profile__session-time">
                {new Date(s.updated_at).toLocaleString()}
              </span>
            </div>
          ))
        ) : (
          <Typography.Text type="secondary">暂无会话</Typography.Text>
        )}
      </Card>
    </div>
  );
}

export default ProfilePage;
