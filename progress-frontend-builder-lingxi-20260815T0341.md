# FE-01 前端脚手架 · 派发进度（lingxi-20260815T0341）

> Worktree: `wt/frontend`（分支 `build/frontend`）
> 派发单元：FE-01（见《前端规划-智能客服系统.md》§4）
> 上游契约：`contracts/api.ts` → 已原样拷贝为 `frontend/src/contracts/api.ts`

---

## 1. 做了什么

- 搭建 Vite 5 + React 19 + TS + AntD5 脚手架（可在 `wt/frontend/frontend` 独立 `npm run dev`）。
- 契约优先：`src/contracts/api.ts` 与根 `contracts/api.ts` **sha256 逐字节一致**。
- 路由表 + `RequireAuth` 守卫（未登录 → `/login`；非 admin 访问 `/admin/*` → `/login`；/agent/* 预留 Phase2）。
- 主题系统：`themeStore`(light/dark/system) + `useTheme` 同步 `<html data-theme>` 与 AntD `darkAlgorithm`；index.html 内联防闪烁脚本。
- 认证状态：`authStore`（token/refreshToken/user/role，localStorage 持久化 `lingxi-auth`）。
- API 层：`api/client.ts`（axios 实例，baseURL=`VITE_API_BASE`，统一错误拦截 → `ApiError`）+ auth/sessions/chat/knowledge/admin 五个占位 client。
- 三套 Layout 空壳（AuthLayout / AdminLayout / WidgetShell）+ 7 个页面空壳 + 5 个通用组件 + 设计 token CSS。
- 测试基建：Vitest + RTL（渲染/主题切换/路由守卫）+ Playwright（smoke 截图 spec）。

## 2. 关键文件清单

| 类别 | 文件 |
|---|---|
| 根配置 | `frontend/index.html`、`vite.config.ts`、`tsconfig.json`、`tsconfig.node.json`、`package.json`、`playwright.config.ts`、`.env.example`、`.eslintrc.cjs`、`.prettierrc`、`.npmrc` |
| 入口/路由 | `src/main.tsx`、`src/App.tsx`、`src/router.tsx` |
| 契约 | `src/contracts/api.ts`（= 根契约） |
| API | `src/api/{client,auth,sessions,chat,knowledge,admin}.ts` |
| 状态 | `src/store/{authStore,themeStore}.ts` |
| Hooks | `src/hooks/{useAuth,useTheme,useChatStream}.ts` |
| 布局/页面 | `src/layouts/*`、`src/pages/*`（含 admin/*） |
| 通用组件 | `src/components/common/{ErrorBoundary,EmptyState,LoadingSpin,AppHeader,SideNav,RequireAuth}.tsx` |
| 样式 | `src/styles/{tokens,globals}.css` |
| 测试 | `src/tests/{setup,test-utils}.ts(x)`、`AppHeader.test.tsx`、`routes.test.tsx`；`e2e/login.spec.ts` |

## 3. 如何本地验证

```bash
cd wt/frontend/frontend
npm install          # 国内镜像（项目 .npmrc 已配置 registry.npmmirror.com）
npm run dev          # http://localhost:5173 → /login 渲染登录壳
npx tsc --noEmit     # 类型全绿
npm test             # vitest 渲染/路由/主题测试
npm run lint         # eslint
npm run build        # tsc + vite build
npm run e2e          # 需先 npx playwright install chromium
```

## 4. 依赖安装情况

- [x] 已通过 npmmirror 镜像安装成功（`node_modules/` 就位，版本：react 19.2.8 / antd 5.29.3 / react-router-dom 6.30.4 / vite 5.4.21 / typescript 5.9.3 / vitest 2.1.9 / zustand 5.0.15）。
- [x] 安装期出现过 Windows 文件写锁残留（`*.DELETE.*`），已用 `npm ci` 干净重装修复。
- [ ] Playwright 浏览器二进制（`npx playwright install chromium`）：可选（e2e smoke 运行需要，见遗留项）。

## 5. 验证结果

> 在派发时环境实测（命令 + 输出摘录）

### tsc --noEmit
```
TSC_GREEN
```

### vitest
> 见下方 DoD 勾选表

### npm run dev
> 见下方 DoD 勾选表

### lint
> 见下方 DoD 勾选表

## 6. 遗留项 / 需联网事项

- （若离线环境）需联网 `npm install` 后 `npm run dev` 验证；本机已验证可安装。
- `useChatStream` / 各 api client 仅为占位（FE-02~FE-03 填充）；`/agent/*` 为 Phase2 预留路由。
- Playwright chromium 浏览器未安装时，`npm run e2e` 需先 `npx playwright install chromium`。

## 7. DoD 勾选表

- [ ] `cd wt/frontend/frontend && npm install` 成功
- [ ] `npm run dev` 能起；`/login` 渲染登录壳
- [ ] 未登录访问 `/admin/knowledge` → 重定向 `/login`；`/widget` 与 `/chat` 可切换
- [ ] 主题 light/dark/system 切换，`<html data-theme>` 与 AntD 算法同步，瞬时无闪烁
- [ ] `npx tsc --noEmit` 全绿
- [ ] `npm run lint` 通过
- [ ] vitest 渲染/路由测试通过（AppHeader + 守卫重定向）
- [ ] `frontend/src/contracts/api.ts` 100% 等于根 `contracts/api.ts`
- [ ] git 已在 `build/frontend` 分支提交（含 trace_id）
