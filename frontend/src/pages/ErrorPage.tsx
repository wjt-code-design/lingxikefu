import { Button, Result } from 'antd';
import { useNavigate } from 'react-router-dom';

interface ErrorPageProps {
  type?: '403' | '404';
}

/** 统一错误页（合并403/404，移除auth-layout hack） */
export function ErrorPage({ type = '404' }: ErrorPageProps) {
  const navigate = useNavigate();
  
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
          <Button type="primary" onClick={() => navigate('/')}>
            返回首页
          </Button>
        }
      />
    </div>
  );
}

export default ErrorPage;
