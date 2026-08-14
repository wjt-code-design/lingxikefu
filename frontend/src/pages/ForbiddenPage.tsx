import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

/**
 * 403 无权限页：已登录但角色不足时展示（替代此前「踢回登录页」的诡异行为）。
 */
export default function ForbiddenPage() {
  const navigate = useNavigate();
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        minHeight: '100vh',
      }}
    >
      <Result
        status="403"
        title="403"
        subTitle="抱歉，你没有权限访问该页面。如需访问，请联系管理员。"
        extra={
          <Button type="primary" onClick={() => navigate('/chat')}>
            返回对话
          </Button>
        }
      />
    </div>
  );
}
