import type { ReactNode } from 'react';
import { Button, Typography } from 'antd';
import {
  AppstoreOutlined,
  ArrowRightOutlined,
  ControlOutlined,
  CustomerServiceOutlined,
  RocketOutlined,
} from '@ant-design/icons';
import { Link } from 'react-router-dom';
import './HelpPage.css';

/**
 * 帮助中心公开页（Phase 3 · 匿名可访问，挂 User 端 WidgetShell 下）
 * - 纯静态内容：三段式帮助（快速开始 / 功能说明 / 常见操作），直接用 JSX 结构呈现。
 * - 不引用任何需登录态的 API。
 * - 视觉：冷灰统一调性 + 海盐蓝点缀；仅消费 tokens.css 变量，克制动效。
 */

interface HelpStep {
  title: string;
  desc: string;
}

interface HelpSection {
  key: string;
  icon: ReactNode;
  title: string;
  desc: string;
  steps: HelpStep[];
}

const HELP_SECTIONS: HelpSection[] = [
  {
    key: 'start',
    icon: <RocketOutlined />,
    title: '快速开始',
    desc: '三步上手，几分钟内完成第一次咨询',
    steps: [
      {
        title: '注册 / 登录',
        desc: '使用邮箱或手机号注册并登录灵犀智能客服，即可开始使用。',
      },
      {
        title: '发起对话',
        desc: '进入对话界面，直接输入问题，或点击快捷问题卡片快速提问。',
      },
      {
        title: '转人工工单',
        desc: '问题未解决时点击「转人工」，系统自动建单，由人工客服跟进处理。',
      },
    ],
  },
  {
    key: 'features',
    icon: <AppstoreOutlined />,
    title: '功能说明',
    desc: '了解对话界面各项能力的用途',
    steps: [
      {
        title: '快捷问题卡片',
        desc: '常见问题一键提问，秒级获得回答，省去手动输入。',
      },
      {
        title: '会话历史',
        desc: '左侧面板随时回顾过往对话，点击即可继续沟通。',
      },
      {
        title: '知识溯源',
        desc: '回答展示引用的知识片段，信息出处可核查、更可信。',
      },
      {
        title: '满意度评价',
        desc: '为回答与服务打分，帮助平台持续优化体验。',
      },
    ],
  },
  {
    key: 'common',
    icon: <ControlOutlined />,
    title: '常见操作',
    desc: '常用功能的快速指引',
    steps: [
      {
        title: '查看我的工单',
        desc: '在「我的工单」中随时查看处理进度与客服回复。',
      },
      {
        title: '个人中心',
        desc: '查看账号信息、剩余额度和最近会话记录。',
      },
      {
        title: '管理后台入口',
        desc: '管理员 / 客服可进入内部工作台处理会话与知识库（需相应权限）。',
      },
    ],
  },
];

export function HelpPage() {
  return (
    <div className="help">
      <div className="help__inner">
        {/* 顶部主标题 + 简介 */}
        <header className="help__hero">
          <Typography.Title className="help__title">帮助中心</Typography.Title>
          <Typography.Paragraph className="help__sub">
            使用指南 · 新手教程 · 常见操作说明，帮你快速熟悉灵犀智能客服
          </Typography.Paragraph>
        </header>

        {/* 三块帮助卡片 */}
        <div className="help__grid">
          {HELP_SECTIONS.map((section, index) => (
            <section key={section.key} className="help__card" aria-labelledby={`help-${section.key}`}>
              <div className="help__card-head">
                <span className="help__card-icon" aria-hidden="true">
                  {section.icon}
                </span>
                <span className="help__card-no" aria-hidden="true">
                  {String(index + 1).padStart(2, '0')}
                </span>
              </div>
              <h2 id={`help-${section.key}`} className="help__card-title">
                {section.title}
              </h2>
              <p className="help__card-desc">{section.desc}</p>
              <ol className="help__steps">
                {section.steps.map((step) => (
                  <li key={step.title} className="help__step">
                    <span className="help__step-num" aria-hidden="true" />
                    <div className="help__step-body">
                      <strong className="help__step-title">{step.title}</strong>
                      <p className="help__step-desc">{step.desc}</p>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          ))}
        </div>

        {/* 联系客服提示条（引导登录后使用 /chat） */}
        <div className="help__contact">
          <span className="help__contact-icon" aria-hidden="true">
            <CustomerServiceOutlined />
          </span>
          <div className="help__contact-text">
            <strong>需要人工帮助？</strong>
            <span>登录后即可与在线客服对话，复杂问题可一键转人工工单。</span>
          </div>
          <Link to="/login" className="help__contact-cta">
            <Button type="primary" icon={<ArrowRightOutlined />}>
              登录咨询
            </Button>
          </Link>
        </div>
      </div>
    </div>
  );
}

export default HelpPage;
