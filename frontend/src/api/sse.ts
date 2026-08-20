/**
 * SSE 帧解析（chat 流式 / 通知长连接共用）。
 *
 * 加固点（审计 #9）：
 * - 拼接帧内全部 `data:` 行（标准 SSE 允许多行 data），而非只取首行——
 *   后端未来加多行 payload 不丢内容；
 * - JSON.parse 失败 console.warn（带帧片段），不再静默吞掉——排查有迹可循。
 */

/** 解析单个 SSE 帧（`\n\n` 分隔后的完整帧）。非 data 帧或解析失败返回 undefined。 */
export function parseSSEFrame<T>(frame: string): T | undefined {
  const data = frame
    .split('\n')
    .filter((l) => l.startsWith('data:'))
    .map((l) => l.slice(5).replace(/^ /, ''))
    .join('\n');
  if (!data) return undefined;
  try {
    return JSON.parse(data) as T;
  } catch (e) {
    console.warn('[SSE] 帧解析失败（已忽略）:', data.slice(0, 120), e);
    return undefined;
  }
}
