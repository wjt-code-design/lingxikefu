/** 订单轨迹结构化解析工具
 *
 * 输入：LLM 生成的纯文本订单列表（可能是 Markdown、可能正在流式生成）。
 * 输出：结构化的订单卡片数据，供 OrderCards 组件渲染。
 *
 * 识别规则（宽松、支持渐进流式）：
 *   1) 单行以 `-` 开头，且包含 `订单` + 订单号（SO\d+）
 *   2) 同一行用 `：` / ` - ` 分隔为：订单头：商品 - 状态，附加信息
 *   3) 自动剔除 `[来源N]` 溯源引用（顾客端不展示）
 *
 * 设计准则：不做过度结构化（LLM 输出不稳定），只做「够用」的识别。
 */

export interface OrderTrackItem {
  /** 原始行（去除来源标签后） */
  raw: string;
  /** 订单号（如 SO2026080199） */
  orderNo?: string;
  /** 商品名 */
  product?: string;
  /** 状态关键字（如"已发货"/"待送装"/"已签收"） */
  status?: string;
  /** 附加信息（运单号、预计送达时间、预约时间、尾号等） */
  detail?: string;
}

export interface OrderTrackResult {
  detected: boolean;
  /** 顶部说明文案（如"资料中有以下订单轨迹："），可选 */
  preamble?: string;
  items: OrderTrackItem[];
  /** 尾部提示（如"请提供您的订单号…"），可选 */
  footer?: string;
}

const ORDER_NO_RE = /SO\d{4,}/i;
const LIST_HEAD_RE = /^\s*[-*]\s*/;
// 状态关键词（按优先级匹配）
const STATUS_KEYWORDS = [
  '已签收',
  '已发货',
  '派送中',
  '运输中',
  '待送装',
  '待发货',
  '已预约',
  '已下单',
  '处理中',
  '已取消',
  '已退款',
  '已完成',
];

/** 从一条订单行中提取字段，行格式大致为：
 *   订单 SOxxxxx：商品名 - 状态，关键信息 [来源N]
 * 或
 *   订单尾号 8823：洗衣机（订单号 SOxxxxxx）- 状态，关键信息 [来源N]
 */
function parseLine(rawLine: string): OrderTrackItem {
  let line = rawLine.replace(LIST_HEAD_RE, '').trim();
  // 去掉 [来源N] 引用（顾客端不展示）
  line = line.replace(/\s*\[来源\d+\]\s*/g, '').trim();

  const item: OrderTrackItem = { raw: line };

  // 订单号
  const orderMatch = line.match(ORDER_NO_RE);
  if (orderMatch) item.orderNo = orderMatch[0].toUpperCase();

  // 商品 + 状态 + 详情：以第一个 `：` 或 `:` 作为分段头，之后用 ` - ` 切商品和状态
  const colonIdx = line.search(/[：:]/);
  let afterColon = colonIdx >= 0 ? line.slice(colonIdx + 1).trim() : line;

  // 去掉前缀中的 "订单" / "订单尾号 XXX" 等描述
  afterColon = afterColon.replace(/^订单(?:尾号\s*\S+\s*)?[：:]?\s*/, '').trim();

  // 先定位状态关键字（用于切分）
  let statusIdx = -1;
  let statusText: string | undefined;
  for (const kw of STATUS_KEYWORDS) {
    const idx = afterColon.indexOf(kw);
    if (idx >= 0 && (statusIdx < 0 || idx < statusIdx)) {
      statusIdx = idx;
      statusText = kw;
    }
  }

  let product: string | undefined;
  let status: string | undefined;
  let detail: string | undefined;

  if (statusIdx > 0) {
    // 商品名 = 状态前的内容（去掉" - "分隔符）
    product = afterColon.slice(0, statusIdx).replace(/\s*-\s*$/, '').trim();
    status = statusText;
    // 详情 = 状态之后（去掉首个分隔符 `，` 或 `,` 或 ` - `）
    detail = afterColon
      .slice(statusIdx + (statusText?.length ?? 0))
      .replace(/^[\s,，\-、:：]+/, '')
      .trim();
  } else {
    // 未识别到状态关键字：尽量按 ` - ` 切分
    const parts = afterColon.split(/\s-\s/);
    if (parts.length >= 2) {
      [product, status, detail] = [parts[0].trim(), parts[1]?.trim(), parts.slice(2).join(' · ').trim() || undefined];
    } else {
      product = afterColon || undefined;
    }
  }

  item.product = product || undefined;
  item.status = status || undefined;
  item.detail = detail || undefined;
  return item;
}

/** 检测一段文本是否是订单轨迹回答，并解析结构化数据。
 * 识别标准：至少 1 条列表行以 `-` 开头且含"订单"关键词，并且至少命中一个订单号 SO\d+。
 * 避免正文提到"订单"二字（如"暂无订单信息"）时误判为订单轨迹。 */
export function detectOrderTrack(content: string): OrderTrackResult {
  const lines = content.split(/\r?\n/);
  const listLines: { idx: number; text: string }[] = [];

  lines.forEach((text, idx) => {
    if (LIST_HEAD_RE.test(text) && /订单/.test(text)) {
      listLines.push({ idx, text });
    }
  });

  const hasOrderNo = listLines.some((l) => ORDER_NO_RE.test(l.text));
  if (!listLines.length || !hasOrderNo) {
    return { detected: false, items: [] };
  }

  const items = listLines.map((l) => parseLine(l.text));

  // 前导说明：取第一条列表行之前的所有非空行拼接
  const firstIdx = listLines[0].idx;
  const preambleLines = lines.slice(0, firstIdx).map((l) => l.trim()).filter(Boolean);
  const preamble = preambleLines.join(' ').replace(/\[来源\d+\]/g, '').trim() || undefined;

  // 尾部：最后一条列表行之后的非空行
  const lastIdx = listLines[listLines.length - 1].idx;
  const footerLines = lines.slice(lastIdx + 1).map((l) => l.trim()).filter(Boolean);
  const footer = footerLines.join(' ').replace(/\[来源\d+\]/g, '').trim() || undefined;

  return { detected: true, preamble, items, footer };
}
