import { Button, Result } from 'antd';

/**
 * 查询错误态（D2）：统一「读取失败 + 重试」入口，替换全站散落的静态错误文案。
 *
 * 用法（配合 react-query）：
 * ```tsx
 * const { data, isLoading, isError, refetch } = useQuery({ ... });
 * if (isLoading) return <Spin />;
 * if (isError || !data) return <QueryErrorState onRetry={() => refetch()} />;
 * ```
 */
export function QueryErrorState({
  title = '数据加载失败',
  description = '请检查网络连接后重试',
  onRetry,
}: {
  title?: string;
  description?: string;
  onRetry?: () => void;
}) {
  return (
    <Result
      status="warning"
      title={title}
      subTitle={description}
      extra={
        onRetry ? (
          <Button type="primary" onClick={onRetry}>
            重试
          </Button>
        ) : null
      }
    />
  );
}

export default QueryErrorState;
