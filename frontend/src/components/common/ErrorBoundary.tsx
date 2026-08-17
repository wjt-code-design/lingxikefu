import { Component, type ErrorInfo, type ReactNode } from 'react';
import { Button, Result } from 'antd';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  message?: string;
}

/**
 * 全局错误边界：子组件树抛出异常时兜底展示错误页，避免整站白屏。
 * 应用于 Router 外层（App.tsx），也可在容器级按需包裹。
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, message: error.message };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // TODO 落地：sendBeacon 上报前端异常（fire-and-forget，页面卸载时也能送达），后端记结构化日志
    console.error('[ErrorBoundary] 捕获异常：', error, info.componentStack);
    try {
      const payload = JSON.stringify({
        message: error.message || String(error),
        stack: (error.stack || '').slice(0, 4000),
        component: (info.componentStack || '').slice(0, 200),
        url: typeof location !== 'undefined' ? location.href : '',
        user_agent: navigator.userAgent || '',
      });
      const url = '/api/v1/telemetry/frontend-error';
      if (navigator.sendBeacon) {
        navigator.sendBeacon(url, new Blob([payload], { type: 'application/json' }));
      } else {
        fetch(url, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: payload,
          keepalive: true,
        }).catch(() => {});
      }
    } catch {
      /* 上报失败静默，不阻断错误边界自身 */
    }
  }

  private reset = () => this.setState({ hasError: false });

  render() {
    if (this.state.hasError) {
      return (
        <Result
          status="error"
          title="页面出错了"
          subTitle={this.state.message}
          extra={
            <Button type="primary" onClick={this.reset}>
              重试
            </Button>
          }
        />
      );
    }
    return this.props.children;
  }
}

export default ErrorBoundary;
