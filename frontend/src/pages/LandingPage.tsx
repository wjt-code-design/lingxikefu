import { Button, Card, Col, Row, Typography } from 'antd';
import { CommentOutlined, SafetyCertificateOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { Link } from 'react-router-dom';

/**
 * 服务首页：未登录访问 / 时展示品牌落地页。
 * 记忆点：网格背景 + 光点漂浮（科技感纵深）
 */
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

      <div className="landing__content">
        <section className="landing__hero">
          <div className="landing__badge">星河智家 · 智能客服</div>
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
              <Col xs={24} sm={8} key={f.title}>
                <Card className="landing__feature">
                  <div className="landing__feature-icon">{f.icon}</div>
                  <Typography.Title level={5}>{f.title}</Typography.Title>
                  <Typography.Text type="secondary">{f.desc}</Typography.Text>
                </Card>
              </Col>
            ))}
          </Row>
        </section>
      </div>
    </div>
  );
}

export default LandingPage;
