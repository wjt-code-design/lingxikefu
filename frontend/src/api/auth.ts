import type { AuthResp, LoginReq, MeResp, RefreshReq, RegisterReq } from '@/contracts/api';

// TODO(FE-02): 认证接口接入 api/client.ts 的 http 实例，并联动 store/authStore.ts
// 签名与契约类型一致（契约优先），本单元仅占位。

export async function login(_req: LoginReq): Promise<AuthResp> {
  throw new Error('TODO(FE-02): login 接口待实现');
}

export async function register(_req: RegisterReq): Promise<AuthResp> {
  throw new Error('TODO(FE-02): register 接口待实现');
}

export async function refresh(_req: RefreshReq): Promise<AuthResp> {
  throw new Error('TODO(FE-02): refresh 接口待实现');
}

export async function me(): Promise<MeResp> {
  throw new Error('TODO(FE-02): me 接口待实现');
}
