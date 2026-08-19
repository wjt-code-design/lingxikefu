import { useEffect } from 'react';
import { useLocation } from 'react-router-dom';
import { Breadcrumb } from 'antd';
import { ROUTE_META } from '@/routes.config';

/**
 * 路由面包屑（demo 路由哲学：展示信息从 ROUTE_META 单一真源派生，不写死在页面里）。
 * 仅对已在 ROUTE_META 登记的路径渲染；未登记路径返回 null。
 *
 * 说明：可见的页面大标题由各页面自身的 <Typography.Title> 负责（避免与本文重复），
 * 这里只补"分组 › 页面"的面包屑导航上下文，并用 config.title 同步 document.title。
 * 无新动画，尊重 prefers-reduced-motion（全局已处理）。
 */
export function RouteChrome() {
  const { pathname } = useLocation();
  const meta = ROUTE_META[pathname];

  useEffect(() => {
    if (meta) document.title = `${meta.title} · 灵犀`;
  }, [meta]);

  if (!meta) return null;

  return (
    <div className="route-chrome">
      <Breadcrumb
        aria-label="breadcrumb"
        items={meta.breadcrumb.map((label) => ({ title: label }))}
      />
    </div>
  );
}

export default RouteChrome;
