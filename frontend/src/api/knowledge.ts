import type { CreateKBReq, DocItem, KBItem, KBListResp, OkResp } from '@/contracts/api';

// TODO(FE-04): 知识库/文档接口接入 api/client.ts，签名与契约一致。

export async function listKnowledgeBases(): Promise<KBListResp> {
  throw new Error('TODO(FE-04): 知识库列表接口待实现');
}

export async function createKnowledgeBase(_req: CreateKBReq): Promise<KBItem> {
  throw new Error('TODO(FE-04): 创建知识库接口待实现');
}

export async function uploadDocument(_kbId: string, _file: File): Promise<DocItem> {
  throw new Error('TODO(FE-04): 上传文档接口待实现');
}

export async function deleteDocument(_docId: string): Promise<OkResp> {
  throw new Error('TODO(FE-04): 删除文档接口待实现');
}
