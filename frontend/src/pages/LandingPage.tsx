import { Button, Card, Col, Row, Typography } from 'antd';
import { CommentOutlined, SafetyCertificateOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';

/** 产品预览区：纯CSS模拟对话界面，展示"灵犀长什么样" */
function ProductPreview() {
  return (
    <div className="landing-preview">
      <div className="preview-topbar">
        <span className="preview-logo">灵犀</span>
        <div className="preview-topbar-right">
          <span className="preview-dot" />
          <span className="preview-dot" />
          <span className="preview-dot" />
        </div>
      </div>
      <div className="preview-body">
        <div className="preview-sidebar">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="preview-sidebar-item" style={{ animationDelay: `${i * 80}ms` }} />
          ))}
        </div>
        <div className="preview-chat">
          <div className="preview-msg preview-msg--user" style={{ animationDelay: '200ms' }}>你好，查一下订单状态</div>
          <div className="preview-msg preview-msg--ai" style={{ animationDelay: '400ms' }}>您好！正在为您查询...</div>
          <div className="preview-msg preview-msg--user" style={{ animationDelay: '600ms' }}>订单号 20260819001</div>
          <div className="preview-msg preview-msg--ai" style={{ animationDelay: '800ms' }}>已发货，预计明天到达 📦</div>
        </div>
        <div className="preview-source">
          <div className="preview-source-label">知识溯源</div>
          <div className="preview-source-line" />
          <div className="preview-source-line preview-source-line--short" />
          <div className="preview-source-line" />
        </div>
      </div>
    </div>
  );
}

/** 品牌落地页：记忆点 = 网格背景 + 光点漂浮 + 产品预览 */
export function LandingPage() {
  return (
    <div className="landing">
      {/* 背景：网格 + 光点 */}
      <div className="landing__bg" aria-hidden="true">
        <div className="landing__grid" />
        <div className="landing__glow landing__glow--1" />
        <div className="landing__glow landing__glow--2" />
        <div className="landing__glow landing__glow--3" />
      </div>

      {/* a11y：内容主体语义化为 main（id 供全局 skip-link 跳转；region 违规随内容入 landmark 消除） */}
      <main id="main-content" className="landing__content">
        <section className="landing__hero">
          <div className="landing__badge">灵犀 · 智能客服</div>
          <Typography.Title className="landing__title">
            灵犀智能客服
            <br />
            <span className="landing__title-accent">随时为您解答</span>
          </Typography.Title>
          <Typography.Paragraph className="landing__sub">
            基于知识库的智能问答，7×24 小时在线。解决不了？一键转人工客服。
          </Typography.Paragraph>
          <div className="landing__cta">
            <Link to="/login">
              <Button type="primary" size="large">
                登录
              </Button>
            </Link>
            <Link to="/widget">
              <Button size="large">免登录体验对话</Button>
            </Link>
          </div>
        </section>

        {/* 产品预览区 */}
        <section className="landing__preview">
          <ProductPreview />
        </section>

        <section className="landing__features">
          <Row gutter={[16, 16]}>
            {[
              {
                icon: <ThunderboltOutlined />,
                title: '即时响应',
                desc: '知识库检索 + 大模型生成，秒级回答常见问题',
              },
              {
                icon: <SafetyCertificateOutlined />,
                title: '工单闭环',
                desc: '转人工自动建单，随时查看处理进度',
              },
              {
                icon: <CommentOutlined />,
                title: '7×24 在线',
                desc: '售后问题不用等，深夜也有人答',
              },
            ].map((f) => (
              // UI 审查低17：md 起三列——窄视口（586px 等）不再三卡并排挤成 3 行
              <Col xs={24} md={8} key={f.title}>
                <Card className="landing__feature">
                  <div className="landing__feature-icon">{f.icon}</div>
                  {/* a11y：level 2（视觉字号由 CSS pin）——h1 下 h5 跳级 axe heading-order */}
                  <Typography.Title level={2} className="landing__feature-title">{f.title}</Typography.Title>
                  <Typography.Text type="secondary">{f.desc}</Typography.Text>
                </Card>
              </Col>
            ))}
          </Row>
        </section>

        {/* Footer */}
        <footer className="landing__footer">
          <span>© 2026 灵犀</span>
          <Link to="/faq">帮助中心</Link>
        </footer>
      </main>
    </div>
  );
}

export default LandingPage;
