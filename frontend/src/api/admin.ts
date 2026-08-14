import type { AdminStats, OkResp, Role, UserListResp } from '@/contracts/api';

// TODO(FE-04): 管理后台接口接入 api/client.ts，签名与契约一致。

export async function getAdminStats(): Promise<AdminStats> {
  throw new Error('TODO(FE-04): 运营统计接口待实现');
}

export async function listUsers(_page: number, _size: number): Promise<UserListResp> {
  throw new Error('TODO(FE-04): 用户列表接口待实现');
}

export async function updateUserRole(_userId: string, _role: Role): Promise<OkResp> {
  throw new Error('TODO(FE-04): 更新用户角色接口待实现');
}
