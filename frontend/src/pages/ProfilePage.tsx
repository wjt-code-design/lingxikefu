import { useQuery } from '@tanstack/react-query';
import { Card, Progress, Spin, Tag, Typography } from 'antd';
import { UserOutlined } from '@ant-design/icons';
import { me as fetchMe } from '@/api/auth';
import { listSessions } from '@/api/sessions';
import { QueryErrorState } from '@/components/common/QueryErrorState';

const ROLE_LABEL: Record<string, string> = { user: '普通用户', agent: '客服', admin: '管理员' };
const ROLE_COLOR: Record<string, string> = { admin: 'gold', agent: 'cyan', user: 'blue' };

/** 个人中心：大头像 + 进度条 + 时间线视觉增强 */
export function ProfilePage() {
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

  const account = me.email || me.phone || '—';
  const quotaLeft = me.quota_left;
  const quotaTotal = me.quota_total; // 从后端获取，可能为 undefined
  const quotaPercent = quotaTotal ? Math.min(100, Math.round((quotaLeft! / quotaTotal) * 100)) : 0;

  // 首字母（邮箱取第一个字，手机取后四位）
  const initial = me.email
    ? me.email.charAt(0).toUpperCase()
    : me.phone
      ? me.phone.slice(-4)
      : '?';

  return (
    <div className="profile">
      {/* 顶部大头像卡 */}
      <Card className="profile__hero-card">
        <div className="profile__hero">
          <div className="profile__avatar">
            <UserOutlined />
            <span className="profile__avatar-initial">{initial}</span>
          </div>
          <div className="profile__hero-info">
            <Typography.Title level={1} className="profile__name">
              {account}
            </Typography.Title>
            <div className="profile__hero-meta">
              <Tag color={ROLE_COLOR[me.role ?? 'user']}>
                {ROLE_LABEL[me.role ?? 'user']}
              </Tag>
              <span className="profile__hero-sep">·</span>
              <span className="profile__hero-quota-label">剩余额度</span>
              <span className="profile__hero-quota">{quotaLeft}</span>
              <span className="profile__hero-quota-total">/ {quotaTotal}</span>
            </div>
          </div>
        </div>
      </Card>

      {/* 额度进度条卡 */}
      <Card className="profile__quota-card">
        <div className="profile__quota-head">
          <Typography.Text strong>对话额度</Typography.Text>
          <Typography.Text type="secondary">
            {quotaTotal ? `已使用 ${quotaTotal - quotaLeft!} / ${quotaTotal}` : '额度信息以管理员设置为准'}
          </Typography.Text>
        </div>
        {quotaTotal ? (
          <Progress
            percent={quotaPercent}
            aria-label={`对话额度剩余 ${quotaPercent}%`}
            strokeColor={quotaPercent > 50 ? '#4DA07F' : quotaPercent > 20 ? '#E0A86A' : '#D97A7A'}
            trailColor="var(--border)"
            showInfo={false}
            size={['100%', 8]}
          />
        ) : (
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            请联系管理员了解您的额度详情
          </Typography.Text>
        )}
      </Card>

      {/* 最近会话时间线 */}
      <Card className="profile__timeline-card" title="最近会话">
        {sessions?.items.length ? (
          <div className="profile__timeline">
            {sessions.items.map((s, i) => (
              <div key={s.id} className="profile__timeline-item">
                <div className="profile__timeline-rail">
                  <div className="profile__timeline-dot" />
                  {i < sessions.items.length - 1 && <div className="profile__timeline-line" />}
                </div>
                <div className="profile__timeline-content">
                  <div className="profile__timeline-title">{s.title || '新对话'}</div>
                  <div className="profile__timeline-time">
                    {new Date(s.updated_at).toLocaleString()}
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <Typography.Text type="secondary">暂无会话记录</Typography.Text>
        )}
      </Card>
    </div>
  );
}

export default ProfilePage;
