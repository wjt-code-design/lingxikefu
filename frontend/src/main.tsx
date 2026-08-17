import '@ant-design/v5-patch-for-react-19';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { bootstrapAuth } from '@/api/auth';
import '@/styles/tokens.css';
import '@/styles/globals.css';

// BUG-15：access token 仅存内存，首屏渲染前先静默续期恢复会话，避免已登录用户闪跳 /login。
// 未登录（无 refreshToken）时 bootstrapAuth 立即返回，不阻塞首屏。
bootstrapAuth().finally(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>
  );
});
