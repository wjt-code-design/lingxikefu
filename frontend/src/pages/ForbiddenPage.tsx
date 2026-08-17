import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

/** 403 无权限页（T4'）：角色无权访问时展示，提供回首页出口。 */
export function ForbiddenPage() {
  const navigate = useNavigate();
  return (
    <Result
      status="403"
      title="403"
      subTitle="抱歉，您没有权限访问该页面"
      extra={
        <Button type="primary" onClick={() => navigate('/')}>
          返回首页
        </Button>
      }
    />
  );
}

export default ForbiddenPage;
