import { http } from '@/api/client';
import type {
  CreateKBReq,
  DocItem,
  DocumentListResp,
  KBItem,
  KBListResp,
  KnowledgeSearchReq,
  KnowledgeSearchResp,
  OkResp,
} from '@/contracts/api';

/** GET /knowledge-bases → KBListResp */
export async function listKnowledgeBases(): Promise<KBListResp> {
  const r = await http.get<KBListResp>('/knowledge-bases');
  return r.data;
}

/** POST /knowledge-bases → KBItem */
export async function createKnowledgeBase(req: CreateKBReq): Promise<KBItem> {
  const r = await http.post<KBItem>('/knowledge-bases', req);
  return r.data;
}

/** GET /knowledge-bases/{kbId}/documents → DocumentListResp */
export async function listDocuments(kbId: string): Promise<DocumentListResp> {
  const r = await http.get<DocumentListResp>(`/knowledge-bases/${kbId}/documents`);
  return r.data;
}

/** POST /knowledge-bases/{kbId}/documents（multipart）→ DocItem */
export async function uploadDocument(kbId: string, file: File): Promise<DocItem> {
  const form = new FormData();
  form.append('file', file);
  const r = await http.post<DocItem>(`/knowledge-bases/${kbId}/documents`, form);
  return r.data;
}

/** DELETE /documents/{docId} → OkResp */
export async function deleteDocument(docId: string): Promise<OkResp> {
  const r = await http.delete<OkResp>(`/documents/${docId}`);
  return r.data;
}

/** POST /knowledge/search → KnowledgeSearchResp（Phase4：语义检索） */
export async function searchKnowledge(req: KnowledgeSearchReq): Promise<KnowledgeSearchResp> {
  const r = await http.post<KnowledgeSearchResp>('/knowledge/search', req);
  return r.data;
}
