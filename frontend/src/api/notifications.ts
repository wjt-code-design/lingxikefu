import { http } from '@/api/client';
import { API_PREFIX, type NotificationItem, type NotificationListResp, type OkResp, type UnreadCountResp } from '@/contracts/api';
import { useAuthStore } from '@/store/authStore';

/**
 * 通知中心接口（agent/admin，user 无通知 403）。
 * 契约见《通知中心SSE-产品契约-2026-08-18.md》：列表/未读/已读 + SSE /stream 实时推送。
 */

/** GET /notifications → 当前角色通知列表（未读在前，时间倒序） */
export async function getNotifications(page = 1, size = 20): Promise<NotificationListResp> {
  const r = await http.get<NotificationListResp>('/notifications', { params: { page, size } });
  return r.data;
}

/** GET /notifications/unread-count → 当前角色未读数（角标轮询兜底） */
export async function getUnreadCount(): Promise<UnreadCountResp> {
  const r = await http.get<UnreadCountResp>('/notifications/unread-count');
  return r.data;
}

/** POST /notifications/{id}/read → 单条标记已读 */
export async function markRead(notificationId: string): Promise<OkResp> {
  const r = await http.post<OkResp>(`/notifications/${notificationId}/read`);
  return r.data;
}

/** POST /notifications/read-all → 全部标记已读 */
export async function markAllRead(): Promise<OkResp> {
  const r = await http.post<OkResp>('/notifications/read-all');
  return r.data;
}

// ---------- SSE 实时推送（fetch + ReadableStream，带 Bearer 头） ----------
// 原生 EventSource 无法设置 Authorization header，故复用 chat 流式同款实现。

/** 后端 /stream 事件协议（JSON 内嵌 event 字段）：connected / notification / ping */
export type NotifySSEEvent =
  | { event: 'connected'; data: { role: string } }
  | { event: 'notification'; data: NotificationItem & { recipient_role?: string } }
  | { event: 'ping'; data: { ts: string } };

export type NotifyStreamHandler = (ev: NotifySSEEvent) => void;

/** 建立通知 SSE 长连接，返回取消函数（组件卸载/登出时调用）。断线重连 + 轮询由调用方兜底。 */
export function subscribeNotifications(onEvent: NotifyStreamHandler): () => void {
  const controller = new AbortController();
  const token = useAuthStore.getState().token;
  const base = import.meta.env.VITE_API_BASE || API_PREFIX;

  void (async () => {
    try {
      const resp = await fetch(`${base}/notifications/stream`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) return;
      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        // 按空行切分 SSE 帧（后端 _sse 输出 `data: {...}\n\n`）
        let idx: number;
        while ((idx = buf.indexOf('\n\n')) >= 0) {
          const frame = buf.slice(0, idx);
          buf = buf.slice(idx + 2);
          const line = frame.split('\n').find((l) => l.startsWith('data: '));
          if (!line) continue;
          try {
            onEvent(JSON.parse(line.slice(6)) as NotifySSEEvent);
          } catch {
            /* 非 JSON 帧（心跳残留）忽略 */
          }
        }
      }
    } catch {
      /* AbortError（卸载）或网络异常：静默，由轮询兜底 */
    }
  })();

  return () => controller.abort();
}
