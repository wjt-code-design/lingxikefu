/**
 * 路由元信息单一真源（demo 路由哲学：路由表只管"路径→组件"，
 * 标题/面包屑/分组等展示与导航派生信息集中在此，由 RouteChrome 消费）。
 *
 * 注意：本文件只描述"展示/导航派生"维度（title / breadcrumb / group），
 * 不描述"路径→懒加载组件"（那部分仍由 router.tsx 声明式持有，避免触及中枢导航）。
 * 二者以绝对路径为键对齐，是同一组路由的两个互补视图，而非双真源。
 */

export type RouteGroup = 'user' | 'agent' | 'admin';

export interface RouteMeta {
  /** 页面标题（同时写入 document.title） */
  title: string;
  /** 面包屑层级，首项为一级分组名 */
  breadcrumb: string[];
  /** 归属分组（供 SideNav / 权限派生复用） */
  group: RouteGroup;
}

export const ROUTE_META: Record<string, RouteMeta> = {
  // —— 用户侧（WidgetShell）——
  // UI 审查低19：['对话','智能对话'] 语义重复 → 分组名与 SideNav「我的」分组对齐
  '/chat': { title: '智能对话', group: 'user', breadcrumb: ['我的', '智能对话'] },
  '/tickets': { title: '我的工单', group: 'user', breadcrumb: ['服务', '我的工单'] },
  '/faq': { title: '帮助中心', group: 'user', breadcrumb: ['服务', '帮助中心'] },
  // UI 审查低20：/help 已重定向到 /faq，不再作为独立菜单/展示项（避免普通用户出现两个"帮助中心"）
  '/profile': { title: '个人中心', group: 'user', breadcrumb: ['账户', '个人中心'] },

  // —— 客服工作台（/agent/*）——
  '/agent/dashboard': { title: '工作台首页', group: 'agent', breadcrumb: ['客服工作台', '工作台首页'] },
  '/agent/sessions': { title: '会话列表', group: 'agent', breadcrumb: ['客服工作台', '会话列表'] },
  '/agent/customers': { title: '客户管理', group: 'agent', breadcrumb: ['客服工作台', '客户管理'] },
  '/agent/tickets': { title: '工单处理', group: 'agent', breadcrumb: ['客服工作台', '工单处理'] },
  '/agent/kb-search': { title: '知识快搜', group: 'agent', breadcrumb: ['客服工作台', '知识快搜'] },

  // —— 运营后台（/admin/*）——
  '/admin/dashboard': { title: '运营总览', group: 'admin', breadcrumb: ['运营后台', '运营总览'] },
  '/admin/knowledge': { title: '知识库', group: 'admin', breadcrumb: ['运营后台', '知识库'] },
  '/admin/users': { title: '用户管理', group: 'admin', breadcrumb: ['运营后台', '用户管理'] },
  '/admin/roles': { title: '角色权限', group: 'admin', breadcrumb: ['运营后台', '角色权限'] },
  '/admin/stats': { title: '数据统计', group: 'admin', breadcrumb: ['运营后台', '数据统计'] },
  '/admin/feedback': { title: '评价反馈', group: 'admin', breadcrumb: ['运营后台', '评价反馈'] },
  '/admin/sessions': { title: '会话审计', group: 'admin', breadcrumb: ['运营后台', '会话审计'] },
  '/admin/settings': { title: '系统设置', group: 'admin', breadcrumb: ['运营后台', '系统设置'] },
  '/admin/logs': { title: '操作日志', group: 'admin', breadcrumb: ['运营后台', '操作日志'] },
  '/admin/eval': { title: '评测中心', group: 'admin', breadcrumb: ['运营后台', '评测中心'] },
};
