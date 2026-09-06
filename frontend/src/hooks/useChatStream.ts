import { useCallback, useEffect, useRef, useState } from 'react';
import { API_PREFIX, type ChatStreamReq, type MessageSource, type SSEEvent } from '@/contracts/api';
import { parseSSEFrame } from '@/api/sse';
import { refreshAccessToken } from '@/api/client';
import { useAuthStore } from '@/store/authStore';

/** 流式对话的本地状态（与契约 SSEStage 对齐 + idle 初始态） */
export type ChatStage = 'idle' | 'retrieving' | 'generating' | 'done' | 'error';

export interface ChatStreamState {
  stage: ChatStage;
  tokens: string;
  reasoning: string; // 思维链累积（开思考时先于 tokens 到达；仅流式过程展示，finalize 不落历史）
  sources: MessageSource[];
  messageId?: string;
  userMessageId?: string; // R2/C4：本次提问的后端真 id（done 回传，本地消息 id 对齐用）
  ticketId?: string; // T1：handoff 建单后携带工单号
  tool?: string; // 批次D/T3：工具回答标记（如 order_query，气泡徽章展示来源）
  answerSource?: string; // 快捷话术回答标记（done.answer_source=quick，SourcePanel 空态区分）
  error?: { code: string; message: string };
}

const INITIAL: ChatStreamState = { stage: 'idle', tokens: '', reasoning: '', sources: [] };

/**
 * 流式兜底超时（L8）。
 * 后端单次 LLM httpx 超时 60s，retrieval + generation 全程可能更长；
 * 此处 120s 防后端进程挂死/丢包导致前端无限等待。
 */
const STREAM_TIMEOUT_MS = 120_000;

/** 后端 SSE 事件分派（stage/intent/token/sources/done/error，见 contracts/api.ts SSEEvent） */
function applyEvent(state: ChatStreamState, ev: SSEEvent): ChatStreamState {
  switch (ev.event) {
    case 'stage':
      return { ...state, stage: ev.data.stage, error: undefined };
    case 'intent':
      // R-2：意图事件供后端落库真实 intent，前端无需状态变更
      return state;
    case 'token':
      return { ...state, tokens: state.tokens + ev.data.delta };
    case 'reasoning':
      // 思维链增量：累积供"思考中"气泡展示（先于 token 到达，用户感知首反馈）
      return { ...state, reasoning: state.reasoning + ev.data.delta };
    case 'sources':
      return { ...state, sources: ev.data.sources };
    case 'done':
      return {
        ...state,
        stage: 'done',
        // 引用校正（2026-09-02）：LLM 路径 done 携带校正后全文（落库/缓存同源），
        // 替换流式累积文本保证「所见=落库」；其他路径无此字段则保留已收 tokens。
        tokens: ev.data.answer ?? state.tokens,
        messageId: ev.data.message_id,
        userMessageId: ev.data.user_message_id, // R2/C4：后端 user 消息真 id
        ticketId: ev.data.ticket_id, // T1：handoff 建单工单号
        tool: ev.data.tool, // 批次D/T3：工具回答标记（无则 undefined）
        answerSource: ev.data.answer_source, // 快捷话术回答（无则 undefined，每轮 done 覆盖）
      };
    case 'error':
      return { ...state, stage: 'error', error: ev.data };
  }
}

/**
 * SSE 流式对话 hook（FE-03 实现）。
 * 原生 EventSource 不支持 POST + body → fetch + ReadableStream：
 * 逐块读取，按空行切分 SSE 帧，解析 `data: {json}` 行后按 event 分派。
 */
export function useChatStream() {
  const [state, setState] = useState<ChatStreamState>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  // B2-7 流式止血：token/reasoning delta 先进 ref 缓冲，rAF 节流合并 setState
  // （≤16ms 一帧）——旧实现每 token 一次 setState → 全树重渲 + Markdown 全文重解析，
  // 长回答时渲染开销随 token 数线性放大。done/error/stop/流终止等末态边界强制
  // 同步 flush 保「所见=完整」；reset/新流开始丢弃缓冲（不跨轮泄漏）。
  const deltaBuf = useRef({ tokens: '', reasoning: '' });
  const rafRef = useRef<number | null>(null);

  const flushDeltas = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    const buf = deltaBuf.current;
    if (!buf.tokens && !buf.reasoning) return;
    deltaBuf.current = { tokens: '', reasoning: '' };
    setState((s) => ({
      ...s,
      tokens: s.tokens + buf.tokens,
      reasoning: s.reasoning + buf.reasoning,
    }));
  }, []);

  const scheduleFlush = useCallback(() => {
    if (rafRef.current !== null) return; // 本帧已排队：后续 delta 搭同一班车
    rafRef.current = requestAnimationFrame(() => {
      rafRef.current = null;
      flushDeltas();
    });
  }, [flushDeltas]);

  // C1：组件卸载时中止进行中的流（防连接泄漏 + 已卸载组件上的 setState）
  useEffect(
    () => () => {
      abortRef.current?.abort();
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    },
    []
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    deltaBuf.current = { tokens: '', reasoning: '' }; // 丢弃在途缓冲（新轮从零）
    setState(INITIAL);
  }, []);

  // P0-4 + H3（外部审查 2026-08-22）：停止生成——abort 的同时必须显式落到终止态。
  // 只 abort 不改状态时，AbortError 非超时分支静默（该静默是为保护 reset 流，不能动），
  // stage 会永远停在 retrieving/generating → streaming 恒真 → 输入区永久禁用。
  // 落到 done 保留已收 tokens，ChatContainer 的 finalize 照常把部分回答并入历史。
  const stop = useCallback(() => {
    flushDeltas(); // B2-7：先并入在途 delta，再落终止态（部分回答不丢尾）
    setState((s) =>
      s.stage === 'retrieving' || s.stage === 'generating' ? { ...s, stage: 'done' } : s
    );
    abortRef.current?.abort();
  }, [flushDeltas]);

  /** 发起流式请求并逐事件更新本地状态。可被 AbortController 中断（新请求/卸载）。 */
  const stream = useCallback(async (req: Omit<ChatStreamReq, 'stream'>): Promise<void> => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const token = useAuthStore.getState().token;

    const base = import.meta.env.VITE_API_BASE || API_PREFIX;
    deltaBuf.current = { tokens: '', reasoning: '' }; // B2-7：新流丢弃上轮在途缓冲
    setState({ stage: 'retrieving', tokens: '', reasoning: '', sources: [] });
    // 兜底超时：超时则中止 fetch 并转超时错误（区分于用户主动 reset 的静默中止）
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, STREAM_TIMEOUT_MS);
    const doFetch = (tok: string | null) =>
      fetch(`${base}/chat/stream`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(tok ? { Authorization: `Bearer ${tok}` } : {}),
        },
        body: JSON.stringify({ ...req, stream: true }),
        signal: controller.signal,
      });
    try {
      let resp = await doFetch(token);
      // B2：access token 过期 → 复用共享 refresh 续期后重试一次（与 axios 拦截器同源；
      // 此前 SSE 走原生 fetch 不经拦截器，401 后必须整页刷新才能继续对话）
      if (resp.status === 401) {
        const newToken = await refreshAccessToken();
        if (!newToken) {
          // 刷新失败（会话已被清空）→ 与拦截器同口径跳登录
          window.location.href = '/login';
          return;
        }
        resp = await doFetch(newToken);
      }
      if (!resp.ok) {
        // P4：HTTP 错误优先透出后端 detail（如 429 配额「今日问答额度已用完」），
        // 而非一律「请求失败（HTTP xxx）请稍后重试」误导为网络问题
        let detail = '';
        try {
          const j = (await resp.json()) as { detail?: string };
          detail = j.detail ?? '';
        } catch {
          /* 非 JSON 错误体：回退通用文案 */
        }
        setState((s) => ({
          ...s,
          stage: 'error',
          error: detail
            ? { code: String(resp.status), message: detail }
            : { code: 'HTTP', message: `请求失败（HTTP ${resp.status}），请稍后重试` },
        }));
        return;
      }
      if (!resp.body) {
        setState((s) => ({
          ...s,
          stage: 'error',
          error: { code: 'HTTP', message: '响应体为空，请稍后重试' },
        }));
        return;
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder('utf-8');
      let buf = '';

      // B2-7：token/reasoning 进缓冲等 rAF 合帧；其余事件（stage/sources/done/error）
      // 是状态跃迁点，先 flush 保证「跃迁前内容完整」，再直接 setState。
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
          const ev = parseSSEFrame<SSEEvent>(frame);
          if (!ev) continue; // 非 data 帧（心跳/空行）忽略
          if (ev.event === 'token') {
            deltaBuf.current.tokens += ev.data.delta;
            scheduleFlush();
            continue;
          }
          if (ev.event === 'reasoning') {
            deltaBuf.current.reasoning += ev.data.delta;
            scheduleFlush();
            continue;
          }
          flushDeltas();
          setState((s) => applyEvent(s, ev));
        }
      }
      // B3：流被服务端中途关闭（read 正常结束但未收到 done/error 事件）→ 落到终止态。
      // 否则 stage 永远停在 retrieving/generating → streaming 恒真 → 输入区永久禁用。
      flushDeltas(); // B2-7：末态前并入在途 delta（错误文案不吞已收内容）
      setState((s) =>
        s.stage === 'retrieving' || s.stage === 'generating'
          ? {
              ...s,
              stage: 'error',
              error: { code: 'STREAM_ENDED', message: '连接中断，回答未完成，请重试' },
            }
          : s
      );
    } catch (e) {
      if ((e as Error).name === 'AbortError') {
        // 用户 reset（silent）或兜底超时（显式报错）
        if (timedOut) {
          flushDeltas(); // B2-7：超时终止前并入在途 delta
          setState((s) => ({
            ...s,
            stage: 'error',
            error: { code: 'TIMEOUT', message: '响应超时，请稍后重试' },
          }));
        }
      } else {
        flushDeltas(); // B2-7：网络错误终止前并入在途 delta
        setState((s) => ({
          ...s,
          stage: 'error',
          error: { code: 'NET', message: '网络连接失败，请检查网络后重试' },
        }));
      }
    } finally {
      clearTimeout(timer);
    }
  }, [flushDeltas, scheduleFlush]);

  return { ...state, reset, stop, stream };
}
