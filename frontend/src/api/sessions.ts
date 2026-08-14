import type { SessionDetail, SessionListReq, SessionListResp } from '@/contracts/api';

// TODO(FE-03): 会话 CRUD 接入 api/client.ts 的 http 实例，签名与契约一致。

export async function listSessions(_req: SessionListReq): Promise<SessionListResp> {
  throw new Error('TODO(FE-03): 会话列表接口待实现');
}

export async function getSessionDetail(_id: string): Promise<SessionDetail> {
  throw new Error('TODO(FE-03): 会话详情接口待实现');
}
