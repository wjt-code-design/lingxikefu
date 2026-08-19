import { useCallback, useState } from 'react';
import { Drawer, Segmented } from 'antd';
import { ReadOutlined, CommentOutlined } from '@ant-design/icons';
import { ChatContainer } from '@/components/chat/ChatContainer';
import type { MessageSource } from '@/contracts/api';
import { HistoryPanel } from './HistoryPanel';
import { SourcePanel } from './SourcePanel';

/**
 * 三栏工作台（海盐蓝）：
 *   左 = 历史对话 + 知识库分类 | 中 = 对话窗口 | 右 = RAG 溯源 + 快捷话术
 * 桌面：CSS grid 三栏常驻；移动端（<768px）：左右栏折叠为顶部切换（抽屉），对话单栏。
 */
export function WorkbenchLayout() {
  const [sources, setSources] = useState<MessageSource[]>([]);
  const onSourcesChange = useCallback((s: MessageSource[]) => setSources(s), []);
  const [mobilePanel, setMobilePanel] = useState<'history' | 'source' | null>(null);
  // P1-3：Composer 的"填入输入框"能力注册到这里，快捷话术点击时调用
  const [fillReply, setFillReply] = useState<((t: string) => void) | null>(null);
  const onRegisterFill = useCallback((fill: (t: string) => void) => setFillReply(() => fill), []);

  return (
    <div className="wb">
      {/* 移动端：顶部栏（左栏/右栏切换入口） */}
      <div className="wb-mobile-bar">
        <Segmented
          size="small"
          options={[
            { label: '历史', value: 'history', icon: <CommentOutlined /> },
            { label: '溯源', value: 'source', icon: <ReadOutlined /> },
          ]}
          value={mobilePanel ?? undefined}
          onChange={(v) => setMobilePanel((v as 'history' | 'source') ?? null)}
        />

      </div>

      <div className="wb-grid">
        <div className="wb-col wb-col--left">
          <HistoryPanel />
        </div>
        <main className="wb-col wb-col--main">
          <ChatContainer onSourcesChange={onSourcesChange} onRegisterFill={onRegisterFill} />
        </main>
        <div className="wb-col wb-col--right">
          <SourcePanel sources={sources} onUseReply={fillReply ?? undefined} />
        </div>
      </div>

      {/* 移动端抽屉：历史左滑（符合返回直觉）、溯源右滑（符合右侧详情心智模型） */}
      <Drawer
        open={mobilePanel === 'history'}
        onClose={() => setMobilePanel(null)}
        placement="left"
        width={300}
        styles={{ body: { padding: 0 } }}
        destroyOnClose
      >
        <HistoryPanel />
      </Drawer>
      <Drawer
        open={mobilePanel === 'source'}
        onClose={() => setMobilePanel(null)}
        placement="right"
        width={300}
        styles={{ body: { padding: 0 } }}
        destroyOnClose
      >
        <SourcePanel sources={sources} onUseReply={fillReply ?? undefined} />
      </Drawer>
    </div>
  );
}
