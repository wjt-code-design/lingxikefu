import { http } from '@/api/client';
import { API_PREFIX, type NotificationItem, type NotificationListResp, type OkResp, type UnreadCountResp } from '@/contracts/api';
import { parseSSEFrame } from '@/api/sse';
import { useAuthStore } from '@/store/authStore';

/**
 * 通知中心接口（agent/admin 看本角色广播+定向本人；user 仅看定向本人，D4 铃铛立项 2026-09-04）。
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

/**
 * 建立通知 SSE 长连接，返回取消函数（组件卸载/登出时调用）。
 *
 * B1-5（2026-09-06 深度审查）：断线自动重连——旧实现单次 fetch 流，服务端关闭/
 * 网络抖动后 for(;;) 退出即静默停更（角标有 30s 轮询兜底，但面板增量从此丢失，
 * 直到用户手动开面板）。现改：流退出后指数退避（base×2^n，上限 30s，×随机抖动
 * 防重连风暴）自动重订阅；重连成功服务端会再发 connected 事件，调用方据此
 * 刷新未读数（NotificationBell 既有分支，零改动）。取消函数置 closed + abort，
 * 之后不再重连。opts.backoffMs 仅测试注入缩短退避基。
 */
export function subscribeNotifications(
  onEvent: NotifyStreamHandler,
  opts: { backoffMs?: number } = {},
): () => void {
  const base = import.meta.env.VITE_API_BASE || API_PREFIX;
  const backoffBase = opts.backoffMs ?? 1000;
  const MAX_BACKOFF_MS = 30_000;
  let closed = false;
  let attempt = 0;
  let controller: AbortController | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const scheduleReconnect = () => {
    if (closed) return;
    const exp = Math.min(backoffBase * 2 ** attempt, MAX_BACKOFF_MS);
    const delay = exp * (0.5 + Math.random() * 0.5); // 抖动：多标签页不同时重连
    attempt += 1;
    timer = setTimeout(() => void connect(), delay);
  };

  const connect = async () => {
    if (closed) return;
    controller = new AbortController();
    const token = useAuthStore.getState().token; // 每次重连取最新 token（静默续期后可用）
    try {
      const resp = await fetch(`${base}/notifications/stream`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) {
        scheduleReconnect();
        return;
      }
      attempt = 0; // 连接成功：退避归零
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
          // #9 加固：拼接全部 data 行再 parse；失败 warn（见 api/sse.ts）
          const ev = parseSSEFrame<NotifySSEEvent>(frame);
          if (!ev) continue; // 非 data 帧（心跳残留）忽略
          onEvent(ev);
        }
      }
      // 服务端正常关闭（done）→ 同样重连
      scheduleReconnect();
    } catch {
      // AbortError（取消/卸载）→ closed 已置位，不再重连；网络异常 → 退避重连
      if (!closed) scheduleReconnect();
    }
  };

  void connect();

  return () => {
    closed = true;
    if (timer) clearTimeout(timer);
    controller?.abort();
  };
}
