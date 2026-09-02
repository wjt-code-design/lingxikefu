import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Button,
  Input,
  Modal,
  Popconfirm,
  Spin,
  Tag,
  Tree,
  Typography,
  Upload,
  message,
} from 'antd';
import type { DataNode, TreeProps } from 'antd/es/tree';
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
import './KnowledgePage.css';

const STATUS_META: Record<DocStatus, { color: string; text: string }> = {
  parsing: { color: 'processing', text: '解析中' },
  embedding: { color: 'processing', text: '向量化中' },
  indexed: { color: 'success', text: '已索引' },
  failed: { color: 'error', text: '失败' },
};

const POLLING_STATUSES: DocStatus[] = ['parsing', 'embedding'];

/**
 * 知识库管理页（FE-04 + T8 + T14 落地）：React Query 统一服务端状态。
 * - kbs 列表 / docs 列表（依赖 kbId）均走 useQuery，缓存 + invalidate 一致
 * - 异步导入轮询：refetchInterval 仅在存在 parsing/embedding 时启用
 * - T14：左侧知识库树形导航（替代顶部 Select），右侧文档列表 / 上传。
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
  // useMemo 稳定引用：下游 F4 通知 useEffect 与多个 useMemo 依赖不随渲染变化
  const kbs: KBItem[] = useMemo(() => kbsData?.items ?? [], [kbsData]);

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

  /* ---- T14：左侧知识库树形导航 ---- */
  // 展开态：数据就绪后默认全部展开（每个知识库节点下带「全部文档」分组）
  const [expandedKeys, setExpandedKeys] = useState<string[]>([]);
  useEffect(() => {
    setExpandedKeys(kbs.map((k) => k.kb_id));
  }, [kbs]);

  // key → kb_id 解析映射（父节点=知识库，子节点=库内文档分组）
  const keyToKb = useMemo(() => {
    const map = new Map<string, string>();
    kbs.forEach((k) => {
      map.set(k.kb_id, k.kb_id);
      map.set(`${k.kb_id}:all`, k.kb_id);
    });
    return map;
  }, [kbs]);

  const treeData: DataNode[] = useMemo(
    () =>
      kbs.map((k) => ({
        key: k.kb_id,
        title: (
          <span className="kb-tree-node">
            <span className="kb-tree-node__name">{k.name}</span>
            <span className="kb-tree-node__meta">{k.doc_count} 篇</span>
          </span>
        ),
        children: [
          {
            key: `${k.kb_id}:all`,
            title: <span className="kb-tree-node kb-tree-node--group">全部文档</span>,
            selectable: false,
            isLeaf: true,
          },
        ],
      })),
    [kbs],
  );

  const handleTreeSelect: TreeProps['onSelect'] = (keys) => {
    const key = typeof keys[0] === 'string' ? keys[0] : undefined;
    if (!key) return;
    const target = keyToKb.get(key);
    if (target) setKbId(target);
  };

  const currentKb = useMemo(() => kbs.find((k) => k.kb_id === kbId), [kbs, kbId]);

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
      <Typography.Title level={1}>知识库管理</Typography.Title>

      <div className="kb-layout page-atmo">
        {/* 左侧：知识库树形导航（a11y：aria-label 与主导航 aside 区分，消 landmark-unique） */}
        <aside className="kb-sidebar" aria-label="知识库分类导航">
          <div className="kb-sidebar__header">
            <span className="kb-sidebar__title">知识库</span>
            <Button
              type="link"
              size="small"
              className="kb-sidebar__new"
              onClick={() => setCreateOpen(true)}
            >
              + 新建
            </Button>
          </div>
          <div className="kb-sidebar__body">
            {kbsLoading ? (
              <div className="kb-sidebar__loading">
                <Spin size="small" />
              </div>
            ) : kbs.length === 0 ? (
              <div className="kb-sidebar__empty">暂无知识库</div>
            ) : (
              <Tree
                className="kb-tree"
                treeData={treeData}
                selectedKeys={kbId ? [kbId] : []}
                expandedKeys={expandedKeys}
                onExpand={(keys) => setExpandedKeys(keys.map(String))}
                onSelect={handleTreeSelect}
                blockNode
              />
            )}
          </div>
        </aside>

        {/* 右侧：文档列表 + 上传 */}
        <section className="kb-main">
          <div className="kb-main__toolbar">
            <span className="kb-main__current">
              {currentKb ? (
                <>
                  当前知识库：<b>{currentKb.name}</b>（{currentKb.doc_count} 篇）
                </>
              ) : (
                '请先在左侧选择知识库'
              )}
            </span>
            <Upload
              accept=".pdf,.docx,.txt,.md"
              showUploadList={false}
              beforeUpload={handleUpload}
              disabled={!kbId || uploading}
            >
              <Button type="primary" loading={uploading} disabled={!kbId || uploading}>
                上传文档
              </Button>
            </Upload>
          </div>

          <AppTable<DocItem>
            rowKey="doc_id"
            loading={docsFetching}
            columns={columns}
            dataSource={docs}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            locale={{ emptyText: kbId ? '该知识库暂无文档' : '请先选择知识库' }}
          />
        </section>
      </div>

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
