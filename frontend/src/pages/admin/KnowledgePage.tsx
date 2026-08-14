import { Typography } from 'antd';
import { EmptyState } from '@/components/common/EmptyState';

/**
 * 知识库管理页（FE-04 实现上传/列表/删除/状态机）。
 */
export function KnowledgePage() {
  return (
    <div className="page">
      <Typography.Title level={3}>知识库管理</Typography.Title>
      <EmptyState
        title="知识库占位"
        description="FE-04 在此渲染 UploadDropzone / DocTable / StatusTag"
      />
    </div>
  );
}

export default KnowledgePage;
