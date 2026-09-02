import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Badge, Button, Dropdown, Empty, Spin, Typography } from 'antd';
import {
  BellOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  FileDoneOutlined,
  MessageOutlined,
  TeamOutlined,
} from '@ant-design/icons';
import {
  getNotifications,
  getUnreadCount,
  markAllRead,
  markRead,
  subscribeNotifications,
  type NotifySSEEvent,
} from '@/api/notifications';
import type { NotificationItem } from '@/contracts/api';
import { useAuthStore } from '@/store/authStore';

/** 事件类型 → 展示标签/图标（未知类型兜底展示原 event_type） */
const EVENT_META: Record<string, { label: string; icon: ReactNode }> = {
  'ticket.created': { label: '新工单', icon: <FileDoneOutlined /> },
  'ticket.transfer': { label: '转人工', icon: <TeamOutlined /> },
  'satisfaction.submitted': { label: '满意度', icon: <MessageOutlined /> },
};

/** 未读角标轮询兜底间隔（SSE 断线时保证角标不丢；打开面板时以列表为准重拉） */
const POLL_MS = 30_000;
/** 面板最多展示条数 */
const PANEL_LIMIT = 30;

/** 格式化时间（MM-DD HH:mm，本地时区）；非法输入返回空串 */
function fmtTime(iso: string): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/**
 * 通知中心铃铛（仅 agent/admin，user 不渲染）。
 * 实时：SSE 推送（fetch+ReadableStream 带 token，复用 chat 流式方案）；
 * 兜底：30s 轮询未读数 + 打开面板重拉列表（SSE 丢帧不影响准确度）。
 */
export function NotificationBell() {
  const role = useAuthStore((s) => s.role);
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const unsubRef = useRef<(() => void) | null>(null);

  const loadList = async () => {
    try {
      setLoading(true);
      const resp = await getNotifications(1, PANEL_LIMIT);
      setItems(resp.items);
    } catch {
      /* 静默：列表失败由轮询/SSE 兜底 */
    } finally {
      setLoading(false);
    }
  };

  const refreshUnread = async () => {
    try {
      const resp = await getUnreadCount();
      setUnread(resp.count);
    } catch {
      /* 静默 */
    }
  };

  useEffect(() => {
    if (role !== 'agent' && role !== 'admin') return;
    void loadList();
    void refreshUnread();
    const timer = window.setInterval(() => void refreshUnread(), POLL_MS);
    const unsub = subscribeNotifications((ev: NotifySSEEvent) => {
      if (ev.event === 'notification') {
        const n = ev.data;
        setItems((prev) => [
          n as NotificationItem,
          ...prev.filter((x) => x.notification_id !== n.notification_id),
        ]);
        if (!n.is_read) setUnread((c) => c + 1);
      } else if (ev.event === 'connected') {
        // 握手成功：以服务端未读数为准重拉（防 SSE 期间累计误差）
        void refreshUnread();
      }
    });
    unsubRef.current = unsub;
    return () => {
      window.clearInterval(timer);
      unsubRef.current?.();
      unsubRef.current = null;
    };
  }, [role]);

  const handleOpenChange = (next: boolean) => {
    setOpen(next);
    if (next) {
      void loadList();
      void refreshUnread();
    }
  };

  const handleRead = async (n: NotificationItem) => {
    if (n.is_read) return;
    try {
      await markRead(n.notification_id);
      setItems((prev) =>
        prev.map((x) => (x.notification_id === n.notification_id ? { ...x, is_read: true } : x)),
      );
      setUnread((c) => Math.max(0, c - 1));
    } catch {
      /* 静默 */
    }
  };

  const handleReadAll = async () => {
    try {
      await markAllRead();
      setItems((prev) => prev.map((x) => ({ ...x, is_read: true })));
      setUnread(0);
    } catch {
      /* 静默 */
    }
  };

  if (role !== 'agent' && role !== 'admin') return null;

  const panel = (
    <div className="notification-panel" data-testid="notification-panel">
      <div className="notification-panel__head">
        <Typography.Text strong>通知中心</Typography.Text>
        <Button
          type="link"
          size="small"
          icon={<CheckOutlined />}
          disabled={unread === 0}
          onClick={() => void handleReadAll()}
        >
          全部已读
        </Button>
      </div>
      <div className="notification-panel__body">
        {loading && items.length === 0 ? (
          <div className="notification-panel__state">
            <Spin size="small" />
          </div>
        ) : items.length === 0 ? (
          <div className="notification-panel__state">
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无通知" />
          </div>
        ) : (
          items.map((n) => {
            const meta = EVENT_META[n.event_type] ?? {
              label: n.event_type,
              icon: <ClockCircleOutlined />,
            };
            return (
              <button
                key={n.notification_id}
                type="button"
                className={`notification-item${n.is_read ? '' : ' notification-item--unread'}`}
                onClick={() => void handleRead(n)}
              >
                <span className="notification-item__icon">{meta.icon}</span>
                <span className="notification-item__main">
                  <span className="notification-item__title">
                    <span className="notification-item__tag">{meta.label}</span>
                    {n.title}
                  </span>
                  <span className="notification-item__content">{n.content}</span>
                  <span className="notification-item__time">{fmtTime(n.created_at)}</span>
                </span>
              </button>
            );
          })
        )}
      </div>
    </div>
  );

  return (
    <Dropdown
      trigger={['click']}
      open={open}
      onOpenChange={handleOpenChange}
      popupRender={() => panel}
      placement="bottomRight"
    >
      <button type="button" className="notification-bell" aria-label="通知中心">
        <Badge count={unread} size="small" overflowCount={99}>
          <BellOutlined className="notification-bell__icon" />
        </Badge>
      </button>
    </Dropdown>
  );
}

export default NotificationBell;
