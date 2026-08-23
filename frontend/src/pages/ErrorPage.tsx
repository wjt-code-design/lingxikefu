import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';
import { useAuthStore } from '@/store/authStore';

interface ErrorPageProps {
  type?: '403' | '404';
}

/** 按角色返回首页 */
function useRoleHome(): string {
  const role = useAuthStore((s) => s.role);
  if (role === 'admin') return '/admin/dashboard';
  if (role === 'agent') return '/agent/dashboard';
  return '/chat';
}

/** 统一错误页（合并403/404，移除auth-layout hack） */
export function ErrorPage({ type = '404' }: ErrorPageProps) {
  const navigate = useNavigate();
  const home = useRoleHome();
  
  const config = type === '403' ? {
    title: '403',
    subTitle: '抱歉，您没有权限访问该页面',
  } : {
    title: '404',
    subTitle: '页面不存在或已被移除',
  };

  return (
    <div className="error-page">
      <Result
        status={type}
        title={config.title}
        subTitle={config.subTitle}
        extra={
          <Button type="primary" onClick={() => navigate(home)}>
            返回首页
          </Button>
        }
      />
    </div>
  );
}

export default ErrorPage;
