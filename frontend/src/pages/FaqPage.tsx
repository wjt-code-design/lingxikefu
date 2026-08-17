import { useMemo, useState, type ReactNode } from 'react';
import { Empty, Input, Typography } from 'antd';
import {
  CarryOutOutlined,
  DownOutlined,
  InfoCircleOutlined,
  QuestionCircleOutlined,
  SearchOutlined,
  SwapOutlined,
  ToolOutlined,
  UserOutlined,
} from '@ant-design/icons';
import './FaqPage.css';

/**
 * FAQ 公开浏览页（Phase 3 · 匿名可访问，挂 User 端 WidgetShell 下）
 * - 数据说明：后端暂无「知识库 → 结构化 FAQ」接口（知识库接口仅 admin 可访问），
 *   故本页使用内置静态 FAQ 常量，页面顶部以说明条提示用户。
 * - 交互：顶部关键词搜索（对 问题/答案 前端过滤）+ 分类 Tab 过滤 + 本地 state 手风琴展开。
 * - 视觉：User 端暖米底 + 海盐蓝点缀 + 大圆角低密度；仅消费 tokens.css 变量。
 */

interface FaqItem {
  q: string;
  a: string;
}

interface FaqCategory {
  key: string;
  name: string;
  icon: ReactNode;
  items: FaqItem[];
}

const FAQ_CATEGORIES: FaqCategory[] = [
  {
    key: 'account',
    name: '账户登录',
    icon: <UserOutlined />,
    items: [
      {
        q: '如何注册灵犀智能客服账号？',
        a: '点击页面右上角「注册」，填写邮箱或手机号并设置密码，勾选服务条款后即可完成注册。注册成功后会自动登录并进入对话界面。',
      },
      {
        q: '登录时提示“账号或密码错误”怎么办？',
        a: '请先确认账号（邮箱/手机号）与密码输入无误，注意字母大小写与多余空格。若仍无法登录，可点击登录页「忘记密码」按提示重置密码后重试。',
      },
      {
        q: '忘记密码如何重置？',
        a: '在登录页点击「忘记密码」，输入注册时使用的邮箱或手机号，系统会发送重置链接或验证码，按提示设置新密码后即可重新登录。',
      },
      {
        q: '可以用哪些方式登录？',
        a: '目前支持账号密码登录，后续将陆续开放验证码快捷登录等方式，敬请期待。',
      },
      {
        q: '为什么我的会话偶尔提示重新登录？',
        a: '出于安全考虑，账号长时间未操作或登录状态过期后需要重新登录。重新登录后，原有的历史会话记录仍会完整保留。',
      },
      {
        q: '如何退出登录？',
        a: '点击右上角用户菜单中的「退出登录」即可。退出后系统不会保存您的密码，请妥善保管账号信息。',
      },
    ],
  },
  {
    key: 'shipping',
    name: '配送物流',
    icon: <CarryOutOutlined />,
    items: [
      {
        q: '下单后多久发货？',
        a: '常规订单通常在 24 小时内发货，预售或定制商品以商品页说明为准。发货后可在订单或物流通知中查看最新进度。',
      },
      {
        q: '如何查询物流信息？',
        a: '可在订单详情或物流通知中查看快递单号与最新轨迹。如物流信息长时间未更新，可联系在线客服协助查询。',
      },
      {
        q: '收货地址填错了如何修改？',
        a: '若订单尚未发货，请尽快联系在线客服修改收货地址；若已发货，建议与快递员直接沟通改派，客服会尽力协助处理。',
      },
      {
        q: '包裹破损或少件如何处理？',
        a: '请在签收时当场验货，发现破损或缺失请拍照留存，并在 24 小时内联系客服提交照片，我们会尽快核实并处理。',
      },
      {
        q: '支持哪些配送方式？',
        a: '平台支持标准快递、同城速递等多种配送方式，具体以结算页可选择的配送方案为准。',
      },
    ],
  },
  {
    key: 'return',
    name: '售后退换',
    icon: <SwapOutlined />,
    items: [
      {
        q: '支持七天无理由退货吗？',
        a: '支持。商品保持完好、不影响二次销售的前提下，可在签收后 7 天内申请无理由退货（定制或特殊商品除外，以商品页说明为准）。',
      },
      {
        q: '如何申请退货退款？',
        a: '在订单页面选择需要退款的商品，提交退货申请并填写原因。客服审核通过后，按指引将商品寄回即可。',
      },
      {
        q: '退款多久能到账？',
        a: '退货商品验收通过后，退款一般会在 1-3 个工作日内原路退回，具体到账时间以支付渠道为准。',
      },
      {
        q: '可以换货吗？',
        a: '可以。如商品存在质量问题或规格不符，可在订单中申请换货，符合条件可享受免费换货服务。',
      },
      {
        q: '退货需要承担运费吗？',
        a: '因商品质量问题导致的退换货运费由平台承担；无理由退货在符合条件时，运费规则以商品页说明为准。',
      },
      {
        q: '赠品需要一并寄回吗？',
        a: '如申请退货，请将商品、配件、赠品及原包装一并寄回，以免影响退款金额与处理进度。',
      },
    ],
  },
  {
    key: 'warranty',
    name: '保修维修',
    icon: <ToolOutlined />,
    items: [
      {
        q: '商品保修期是多久？',
        a: '不同品类保修期不同，一般为 1 年，具体以商品页或随附保修卡上的说明为准。',
      },
      {
        q: '保修期内出现故障如何报修？',
        a: '请通过对话界面转人工客服或提交工单，提供订单号与故障描述，客服会为您登记并安排检测维修。',
      },
      {
        q: '哪些情况不在保修范围内？',
        a: '人为损坏、私自拆机、进水、正常磨损以及不可抗力导致的损坏通常不在保修范围内，具体以保修政策为准。',
      },
      {
        q: '维修需要多长时间？',
        a: '一般维修周期为 7-15 个工作日（不含往返物流时间），复杂故障以客服告知的预估时间为准。',
      },
      {
        q: '过了保修期还能维修吗？',
        a: '可以。过保商品支持有偿维修，客服会先为您提供检测与报价，确认后再进行维修。',
      },
    ],
  },
  {
    key: 'usage',
    name: '使用帮助',
    icon: <QuestionCircleOutlined />,
    items: [
      {
        q: '如何快速开始一次对话？',
        a: '登录后即可直接输入问题，或点击首页快捷问题卡片快速提问，灵犀客服会在几秒内给出回答。',
      },
      {
        q: '回答内容来自哪里？如何溯源？',
        a: '回答由平台知识库检索结合智能生成，右侧「溯源」区会展示引用的知识片段，方便您核对信息来源。',
      },
      {
        q: '可以查看历史会话记录吗？',
        a: '可以。左侧「会话历史」面板可查看过往对话，点击即可回到对应会话继续沟通。',
      },
      {
        q: '如何对回答进行评价？',
        a: '每条回答下方提供“有帮助 / 没帮助”评价按钮，您也可以对客服服务进行满意度评分，帮助我们持续改进。',
      },
      {
        q: '问题没解决，如何转人工？',
        a: '点击对话窗口中的「转人工」按钮，系统会自动创建工单并优先安排人工客服为您跟进。',
      },
      {
        q: '可以切换界面主题吗？',
        a: '可以。点击顶栏「主题切换」可在浅色与柔和两种浅色风格间选择，并支持跟随系统设置。',
      },
    ],
  },
];

/** 全部问题展平（供搜索 / 计数） */
const FLAT_ITEMS: { item: FaqItem; cat: FaqCategory }[] = FAQ_CATEGORIES.flatMap((cat) =>
  cat.items.map((item) => ({ item, cat })),
);

export function FaqPage() {
  const [query, setQuery] = useState('');
  const [activeCat, setActiveCat] = useState('all');
  const [openId, setOpenId] = useState<string | null>(null);

  /** 分类 Tab 点击：切换分类并收起已展开的条目 */
  const handleCatChange = (key: string) => {
    setActiveCat(key);
    setOpenId(null);
  };

  /** 根据关键词过滤（对问题与答案做前端匹配） */
  const filtered = useMemo(() => {
    const kw = query.trim().toLowerCase();
    if (!kw) return FLAT_ITEMS;
    return FLAT_ITEMS.filter(({ item }) => {
      const text = `${item.q} ${item.a}`.toLowerCase();
      return text.includes(kw);
    });
  }, [query]);

  /** 当前展示的分类（搜索时覆盖为全部分类，展示带分类标签的平铺结果） */
  const visibleCats = useMemo(() => {
    const kw = query.trim();
    if (kw) return FAQ_CATEGORIES;
    return activeCat === 'all'
      ? FAQ_CATEGORIES
      : FAQ_CATEGORIES.filter((c) => c.key === activeCat);
  }, [activeCat, query]);

  const hasResult = filtered.length > 0;

  const toggleItem = (id: string) => {
    setOpenId((cur) => (cur === id ? null : id));
  };

  return (
    <div className="faq">
      <div className="faq__inner">
        {/* 顶部说明条 */}
        <div className="faq__notice" role="note">
          <InfoCircleOutlined aria-hidden="true" />
          <span>当前为通用帮助内容，知识库结构化 FAQ 将在后续版本接入</span>
        </div>

        {/* 头部 */}
        <header className="faq__header">
          <Typography.Title className="faq__title">常见问题</Typography.Title>
          <Typography.Paragraph className="faq__sub">
            搜索关键词或按分类浏览，快速找到你关心的问题
          </Typography.Paragraph>
        </header>

        {/* 搜索框 */}
        <Input
          className="faq__search"
          size="large"
          allowClear
          prefix={<SearchOutlined aria-hidden="true" />}
          placeholder="搜索常见问题，如：退款、物流、保修…"
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setOpenId(null);
          }}
          aria-label="搜索常见问题"
        />

        {/* 分类 Tab */}
        <div className="faq__cats" role="tablist" aria-label="问题分类">
          <button
            type="button"
            className={`faq__cat ${activeCat === 'all' && !query ? 'is-active' : ''}`}
            onClick={() => handleCatChange('all')}
            role="tab"
            aria-selected={activeCat === 'all' && !query}
          >
            全部
          </button>
          {FAQ_CATEGORIES.map((cat) => (
            <button
              type="button"
              key={cat.key}
              className={`faq__cat ${activeCat === cat.key && !query ? 'is-active' : ''}`}
              onClick={() => handleCatChange(cat.key)}
              role="tab"
              aria-selected={activeCat === cat.key && !query}
            >
              <span className="faq__cat-icon" aria-hidden="true">
                {cat.icon}
              </span>
              {cat.name}
            </button>
          ))}
        </div>

        {/* 搜索结果提示 / 空态 */}
        {query.trim() ? (
          <div className="faq__result-tip">
            {hasResult ? (
              <span>
                共找到 <strong>{filtered.length}</strong> 条相关问题
              </span>
            ) : null}
          </div>
        ) : null}

        {/* 问答列表 */}
        {!hasResult ? (
          <Empty
            className="faq__empty"
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="没有找到相关问题，换个关键词试试"
          />
        ) : query.trim() ? (
          /* 搜索态：平铺展示，每条带分类标签 */
          <div className="faq__list">
            {filtered.map(({ item, cat }, idx) => {
              const id = `${cat.key}::search-${idx}`;
              const isOpen = openId === id;
              return (
                <FaqItemRow
                  key={id}
                  id={id}
                  item={item}
                  catName={cat.name}
                  isOpen={isOpen}
                  onToggle={toggleItem}
                />
              );
            })}
          </div>
        ) : (
          /* 分类态：按当前分类分组展示 */
          visibleCats.map((cat) => (
            <section key={cat.key} className="faq__section" aria-labelledby={`faq-sec-${cat.key}`}>
              <h3 id={`faq-sec-${cat.key}`} className="faq__section-title">
                <span className="faq__section-icon" aria-hidden="true">
                  {cat.icon}
                </span>
                {cat.name}
                <span className="faq__section-count">{cat.items.length}</span>
              </h3>
              <div className="faq__list">
                {cat.items.map((item, idx) => {
                  const id = `${cat.key}::${idx}`;
                  const isOpen = openId === id;
                  return (
                    <FaqItemRow
                      key={id}
                      id={id}
                      item={item}
                      isOpen={isOpen}
                      onToggle={toggleItem}
                    />
                  );
                })}
              </div>
            </section>
          ))
        )}
      </div>
    </div>
  );
}

interface FaqItemRowProps {
  id: string;
  item: FaqItem;
  catName?: string;
  isOpen: boolean;
  onToggle: (id: string) => void;
}

/** 单条问答（手风琴，本地 state 控制展开） */
function FaqItemRow({ id, item, catName, isOpen, onToggle }: FaqItemRowProps) {
  return (
    <div className={`faq-item ${isOpen ? 'is-open' : ''}`}>
      <button
        type="button"
        className="faq-item__q"
        onClick={() => onToggle(id)}
        aria-expanded={isOpen}
        aria-controls={`faq-a-${id}`}
      >
        <span className="faq-item__q-icon" aria-hidden="true">
          <QuestionCircleOutlined />
        </span>
        <span className="faq-item__q-text">{item.q}</span>
        {catName ? <span className="faq-item__cat">{catName}</span> : null}
        <span className={`faq-item__chevron ${isOpen ? 'is-open' : ''}`} aria-hidden="true">
          <DownOutlined />
        </span>
      </button>
      {isOpen ? (
        <div id={`faq-a-${id}`} className="faq-item__a">
          <p>{item.a}</p>
        </div>
      ) : null}
    </div>
  );
}

export default FaqPage;
