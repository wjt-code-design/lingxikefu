import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import type { UploadProps } from 'antd/es/upload';
import { AppTable } from '@/components/common/AppTable';
import {
  createKnowledgeBase,
  deleteDocument,
  listDocuments,
  listKnowledgeBases,
  uploadDocument,
} from '@/api/knowledge';
import type { ApiError, DocItem, DocStatus, KBItem } from '@/contracts/api';

const STATUS_META: Record<DocStatus, { color: string; text: string }> = {
  parsing: { color: 'processing', text: '解析中' },
  embedding: { color: 'processing', text: '向量化中' },
  indexed: { color: 'success', text: '已索引' },
  failed: { color: 'error', text: '失败' },
};

const POLLING_STATUSES: DocStatus[] = ['parsing', 'embedding'];

/**
 * 知识库管理页（FE-04 + T8 落地）：React Query 统一服务端状态。
 * - kbs 列表 / docs 列表（依赖 kbId）均走 useQuery，缓存 + invalidate 一致
 * - 异步导入轮询：refetchInterval 仅在存在 parsing/embedding 时启用
 */
export function KnowledgePage() {
  const [kbId, setKbId] = useState<string | undefined>();
  const [uploading, setUploading] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const queryClient = useQueryClient();

  const { data: kbsData, isLoading: kbsLoading } = useQuery({
    queryKey: ['kbs'],
    queryFn: listKnowledgeBases,
  });
  const kbs: KBItem[] = kbsData?.items ?? [];

  const { data: docsData, isFetching: docsFetching } = useQuery({
    queryKey: ['docs', kbId],
    queryFn: () => listDocuments(kbId!),
    enabled: !!kbId,
    // 异步导入轮询：存在 parsing/embedding 时每 2s 刷新，全部稳定后停止
    refetchInterval: (query) => {
      const items: DocItem[] = query.state.data?.items ?? [];
      return items.some((d) => POLLING_STATUSES.includes(d.status)) ? 2000 : false;
    },
  });
  // useMemo：docs 引用稳定，F4 状态迁移 useEffect 依赖不随渲染变化
  const docs = useMemo(() => docsData?.items ?? [], [docsData]);

  // F4：导入完成通知——轮询检测到文档从 parsing/embedding → indexed 时提示一次
  const prevStatusRef = useRef<Record<string, DocStatus>>({});
  useEffect(() => {
    const prev = prevStatusRef.current;
    for (const d of docs) {
      const before = prev[d.doc_id];
      if (d.status === 'indexed' && before && POLLING_STATUSES.includes(before)) {
        message.success(`「${d.name}」导入完成`);
        break;
      }
    }
    prevStatusRef.current = Object.fromEntries(docs.map((d) => [d.doc_id, d.status]));
  }, [docs]);

  const handleCreate = () => {
    if (!newName.trim()) {
      message.warning('请输入知识库名称');
      return;
    }
    setCreating(true);
    createKnowledgeBase({ name: newName.trim() })
      .then((kb) => {
        message.success('知识库已创建');
        setCreateOpen(false);
        setNewName('');
        setKbId(kb.kb_id);
        queryClient.invalidateQueries({ queryKey: ['kbs'] });
      })
      .catch((e: unknown) => message.error((e as ApiError).message || '创建失败'))
      .finally(() => setCreating(false));
  };

  const handleUpload: UploadProps['beforeUpload'] = (file) => {
    if (!kbId) {
      message.warning('请先选择知识库');
      return Upload.LIST_IGNORE;
    }
    setUploading(true);
    uploadDocument(kbId, file as File)
      .then(() => {
        message.success('已上传，正在导入');
        queryClient.invalidateQueries({ queryKey: ['docs', kbId] });
        queryClient.invalidateQueries({ queryKey: ['kbs'] });
      })
      .catch((e: unknown) => message.error((e as ApiError).message || '上传失败'))
      .finally(() => setUploading(false));
    return false; // 手动上传，阻止 antd 自动上传
  };

  const handleDelete = (docId: string) => {
    deleteDocument(docId)
      .then(() => {
        message.success('已删除');
        if (kbId) {
          queryClient.invalidateQueries({ queryKey: ['docs', kbId] });
          queryClient.invalidateQueries({ queryKey: ['kbs'] });
        }
      })
      .catch((e: unknown) => message.error((e as ApiError).message || '删除失败'));
  };

  const columns: ColumnsType<DocItem> = [
    { title: '文件名', dataIndex: 'name' },
    {
      title: '状态',
      dataIndex: 'status',
      render: (s: DocStatus) => <Tag color={STATUS_META[s].color}>{STATUS_META[s].text}</Tag>,
    },
    { title: '切片数', dataIndex: 'chunks', width: 100 },
    {
      title: '操作',
      width: 100,
      render: (_, r) => (
        <Popconfirm
          title="确认删除该文档？"
          onConfirm={() => handleDelete(r.doc_id)}
          okText="删除"
          cancelText="取消"
        >
          <Button type="link" danger>
            删除
          </Button>
        </Popconfirm>
      ),
    },
  ];

  return (
    <div className="page">
      <Typography.Title level={3}>知识库管理</Typography.Title>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          style={{ minWidth: 240 }}
          placeholder="选择知识库"
          loading={kbsLoading}
          value={kbId}
          onChange={setKbId}
          options={kbs.map((k) => ({ label: `${k.name}（${k.doc_count} 篇）`, value: k.kb_id }))}
        />
        <Button type="primary" onClick={() => setCreateOpen(true)}>
          新建知识库
        </Button>
        <Upload
          accept=".pdf,.docx,.txt,.md"
          showUploadList={false}
          beforeUpload={handleUpload}
          disabled={!kbId || uploading}
        >
          <Button loading={uploading}>上传文档</Button>
        </Upload>
      </Space>

      <AppTable<DocItem>
        rowKey="doc_id"
        loading={docsFetching}
        columns={columns}
        dataSource={docs}
        pagination={{ pageSize: 10, showSizeChanger: false }}
        locale={{ emptyText: kbId ? '该知识库暂无文档' : '请先选择知识库' }}
      />

      <Modal
        title="新建知识库"
        open={createOpen}
        onOk={handleCreate}
        onCancel={() => setCreateOpen(false)}
        confirmLoading={creating}
        okText="创建"
        cancelText="取消"
      >
        <Input
          placeholder="知识库名称"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
          maxLength={255}
          onPressEnter={handleCreate}
        />
      </Modal>
    </div>
  );
}

export default KnowledgePage;
