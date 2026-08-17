import { http } from './client';
import type { CustomerListResp } from '@/contracts/api';

/** 客户画像列表（agent/admin） */
export function listCustomers(page = 1, size = 20): Promise<CustomerListResp> {
  return http
    .get<CustomerListResp>('/customers', { params: { page, size } })
    .then((r) => r.data);
}
