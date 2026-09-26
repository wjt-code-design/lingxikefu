import '@ant-design/v5-patch-for-react-19';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { bootstrapAuth } from '@/api/auth';
import { startCrossTabAuthSync } from '@/store/authStore';
import '@/styles/tokens.css';
import '@/styles/globals.css';

// D16：先挂跨标签页监听，再续期——另一标签页轮换 refreshToken 时本页立刻采纳，
// 用户切回来不必先吃一个 401（401 兜底见 api/client.ts 的 doRefresh）。
startCrossTabAuthSync();

// BUG-15：access token 仅存内存，首屏渲染前先静默续期恢复会话，避免已登录用户闪跳 /login。
// 未登录（无 refreshToken）时 bootstrapAuth 立即返回，不阻塞首屏。
bootstrapAuth().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
});
