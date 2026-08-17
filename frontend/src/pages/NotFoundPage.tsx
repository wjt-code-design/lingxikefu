import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

/** 404 未找到页（海盐蓝空态卡，未知路由不再静默重定向首页）。 */
export function NotFoundPage() {
  const navigate = useNavigate();
  return (
    <div className="auth-layout">
      <Result
        status="404"
        title="404"
        subTitle="页面不存在或已被移除"
        extra={
          <Button type="primary" onClick={() => navigate('/', { replace: true })}>
            返回首页
          </Button>
        }
      />
    </div>
  );
}

export default NotFoundPage;
