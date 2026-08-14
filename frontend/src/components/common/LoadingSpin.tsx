import { Spin, Typography } from 'antd';

interface LoadingSpinProps {
  tip?: string;
  fullHeight?: boolean;
}

/**
 * 通用加载态：居中 Spin，可选提示文案；fullHeight 时撑满父容器。
 */
export function LoadingSpin({ tip = '加载中…', fullHeight = true }: LoadingSpinProps) {
  return (
    <div className={fullHeight ? 'loading-spin loading-spin--full' : 'loading-spin'}>
      <Spin>
        <Typography.Text type="secondary">{tip}</Typography.Text>
      </Spin>
    </div>
  );
}

export default LoadingSpin;
