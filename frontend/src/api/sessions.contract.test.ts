import { describe, expect, it, vi } from 'vitest';

vi.mock('@/api/client', () => ({
  http: { post: vi.fn(() => Promise.resolve({ data: {} })) },
}));

import { http } from '@/api/client';
import { suggestReply } from './sessions';

const mockedPost = vi.mocked(http.post);

/** 测试盲区补齐（P2-⑤）：suggest 超时契约——前端单请求超时必须大于后端 LLM 25s 上限。
 *  类同后端 test_sessions_suggest.py::test_suggest_passes_short_timeout_to_llm（断言 25s），
 *  两端共同钉死"后端 25s 内必返回、前端 35s 等待"的契约，防止此后单边改动造成假失败。 */
describe('坐席辅助超时契约', () => {
  it('suggest 单请求超时 35s > 后端 LLM 25s 上限', async () => {
    await suggestReply('s-1', '问题原文', false);
    expect(mockedPost).toHaveBeenCalledTimes(1);
    const [, , options] = mockedPost.mock.calls[0] as [string, unknown, { timeout?: number }];
    expect(options?.timeout).toBe(35_000);
    // 严格守住"前端 > 后端"关系：后端超时上调时此处即红，逼两侧同步修订
    expect((options?.timeout ?? 0) > 25_000).toBe(true);
  });
});