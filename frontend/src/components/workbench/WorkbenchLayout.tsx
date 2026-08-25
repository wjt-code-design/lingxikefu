import { useCallback, useState } from 'react';
import { Button, Drawer } from 'antd';
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
  // 快捷话术回答标记（done.answer_source）：SourcePanel 据此区分「预置话术无引用」空态
  const [answerSource, setAnswerSource] = useState<string | undefined>(undefined);
  const onAnswerSourceChange = useCallback((v: string | undefined) => setAnswerSource(v), []);
  // A+：历史/溯源默认收起，由对话工具栏图标触发抽屉（桌面/移动统一）；panel 取代原 mobilePanel
  const [panel, setPanel] = useState<'history' | 'source' | null>(null);
  // P1-3：Composer 的"填入输入框"能力注册到这里，快捷话术点击时调用
  const [fillReply, setFillReply] = useState<((t: string) => void) | null>(null);
  const onRegisterFill = useCallback((fill: (t: string) => void) => setFillReply(() => fill), []);

  return (
    <div className="wb">
      {/* A+：默认满宽对话（仅全局导航占侧栏）；历史/溯源收起为抽屉，对话区拿到最大宽度 */}
      <div className="wb-grid">
        <main className="wb-col wb-col--main">
          <div className="wb-chat-toolbar">
            <Button type="text" icon={<CommentOutlined />} onClick={() => setPanel('history')}>
              历史会话
            </Button>
            <Button type="text" icon={<ReadOutlined />} onClick={() => setPanel('source')}>
              溯源来源
            </Button>
          </div>
          <ChatContainer onSourcesChange={onSourcesChange} onAnswerSourceChange={onAnswerSourceChange} onRegisterFill={onRegisterFill} />
        </main>
      </div>

      {/* 抽屉：历史左滑、溯源右滑（桌面/移动一致） */}
      <Drawer
        open={panel === 'history'}
        onClose={() => setPanel(null)}
        placement="left"
        width={300}
        styles={{ body: { padding: 0 } }}
        destroyOnClose
      >
        <HistoryPanel />
      </Drawer>
      <Drawer
        open={panel === 'source'}
        onClose={() => setPanel(null)}
        placement="right"
        width={300}
        styles={{ body: { padding: 0 } }}
        destroyOnClose
      >
        <SourcePanel sources={sources} answerSource={answerSource} onUseReply={fillReply ?? undefined} />
      </Drawer>
    </div>
  );
}
