import type { ReactNode } from 'react';
import { Outlet } from 'react-router-dom';
import './AuthLayout.css';

/**
 * 认证外壳（Phase 2 task 12 · 品牌化改造）：
 * 左右分屏——左品牌区（海盐蓝渐变 + SVG 插画 + 标语），右表单区（白底）。
 * 窄屏（<768px）品牌区压缩为顶部品牌条，表单全宽。
 * 仅视觉布局改造，不参与登录/注册逻辑（表单逻辑在各页面内）。
 */
export function AuthLayout({ children }: { children?: ReactNode }) {
  return (
    <div className="auth-shell">
      {/* 左：品牌区 */}
      <aside className="auth-brand">
        <div className="auth-brand__inner">
          <div className="auth-brand__logo">
            <span className="auth-brand__logo-mark" aria-hidden="true" />
            灵犀 · Lingxi
          </div>
          <BrandIllustration />
          <h2 className="auth-brand__slogan">7×24 智能客服，随时为你解答</h2>
          <ul className="auth-brand__features">
            <li>AI 智能应答</li>
            <li aria-hidden="true">·</li>
            <li>7×24 在线</li>
            <li aria-hidden="true">·</li>
            <li>知识库驱动</li>
          </ul>
        </div>
      </aside>

      {/* 右：表单区 */}
      <main className="auth-form">
        <div className="auth-form__inner">{children ?? <Outlet />}</div>
      </main>
    </div>
  );
}

/** 品牌插画：海盐蓝智能客服 SVG（插画内部配色允许硬编码，规范例外） */
function BrandIllustration() {
  return (
    <svg
      className="auth-brand__art"
      viewBox="0 0 360 320"
      role="img"
      aria-label="7×24 智能客服插画"
    >
      {/* 背景装饰圆环 */}
      <circle
        cx="180"
        cy="150"
        r="120"
        fill="none"
        stroke="#FFFFFF"
        strokeOpacity="0.25"
        strokeWidth="2"
        strokeDasharray="6 10"
      />
      {/* 漂浮光点 */}
      <circle cx="58" cy="66" r="9" fill="#FFFFFF" opacity="0.7" />
      <circle cx="306" cy="52" r="6" fill="#FFFFFF" opacity="0.5" />
      <circle cx="66" cy="262" r="7" fill="#FFFFFF" opacity="0.55" />
      <circle cx="300" cy="238" r="11" fill="#FFFFFF" opacity="0.45" />
      <circle cx="244" cy="34" r="5" fill="#FFFFFF" opacity="0.6" />
      <circle cx="118" cy="30" r="4" fill="#FFFFFF" opacity="0.55" />

      {/* 主聊天气泡 */}
      <rect x="56" y="52" width="248" height="198" rx="42" fill="#FFFFFF" opacity="0.96" />
      <path d="M104 248 L92 290 L156 250 Z" fill="#FFFFFF" opacity="0.96" />

      {/* 24/7 在线徽标 */}
      <g>
        <rect x="228" y="70" width="62" height="26" rx="13" fill="#539FD8" />
        <text
          x="259"
          y="88"
          textAnchor="middle"
          fontSize="13"
          fontWeight="700"
          fill="#FFFFFF"
          fontFamily="inherit"
        >
          24/7
        </text>
      </g>

      {/* 智能客服机器人 */}
      <g>
        {/* 天线 */}
        <line x1="180" y1="128" x2="180" y2="104" stroke="#539FD8" strokeWidth="8" strokeLinecap="round" />
        <circle cx="180" cy="98" r="9" fill="#539FD8" />
        {/* 耳麦 */}
        <rect x="126" y="160" width="10" height="26" rx="5" fill="#7FB6DD" />
        <rect x="224" y="160" width="10" height="26" rx="5" fill="#7FB6DD" />
        {/* 头 */}
        <rect x="130" y="128" width="100" height="92" rx="30" fill="#BFE0F6" />
        {/* 眼睛 */}
        <circle cx="158" cy="166" r="8" fill="#2F3E4E" />
        <circle cx="202" cy="166" r="8" fill="#2F3E4E" />
        <circle cx="161" cy="163" r="2.6" fill="#FFFFFF" />
        <circle cx="205" cy="163" r="2.6" fill="#FFFFFF" />
        {/* 笑脸 */}
        <path d="M158 190 Q180 208 202 190" stroke="#2F3E4E" strokeWidth="5.5" fill="none" strokeLinecap="round" />
        {/* 腮红 */}
        <circle cx="142" cy="186" r="6" fill="#F6C38E" opacity="0.75" />
        <circle cx="218" cy="186" r="6" fill="#F6C38E" opacity="0.75" />
      </g>

      {/* 星芒装饰 */}
      <path d="M286 168 l6 12 12 6 -12 6 -6 12 -6 -12 -12 -6 12 -6 Z" fill="#FFFFFF" opacity="0.8" />
      <path d="M52 176 l4 8 8 4 -8 4 -4 8 -4 -8 -8 -4 8 -4 Z" fill="#FFFFFF" opacity="0.7" />
    </svg>
  );
}

export default AuthLayout;
