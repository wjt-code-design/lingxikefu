import {
  Alert,
  Button,
  Card,
  Form,
  InputNumber,
  Select,
  Switch,
  Typography,
  message,
} from 'antd';
import './SettingsPage.css';

/** 模型可选列表（占位，待后端配置接口开放后拉取真实模型清单） */
const MODEL_OPTIONS = [{ value: 'glm-4.5-air', label: 'glm-4.5-air' }];

/** 示例值（当前仅展示；配置项实际由后端 .env 管理，运行时不可改） */
const EXAMPLE_VALUES = {
  model: 'glm-4.5-air',
  temperature: 0.7,
  top_p: 0.9,
  top_k: 5,
  similarity_threshold: 0.65,
  answer_cache: true,
  rpm_limit: 20,
  rate_limit_enabled: true,
  daily_quota: 100,
  quota_warn_ratio: 0.8,
};

/**
 * 系统设置页（Phase3）：模型 / RAG 阈值 / 限流 / 配额 的可视化表单界面。
 * 后端当前无配置接口（配置在 .env，运行时不可改）→ 只读展示 + 表单骨架：
 * Form disabled 整体禁编辑，值均为占位/示例值，明确标注「待后端配置接口开放」。
 * 不调用任何不存在的 API。
 */
export function SettingsPage() {
  const [form] = Form.useForm();

  const handleSave = () => {
    message.info('待后端配置接口开放后启用');
  };

  return (
    <div className="page settings-page">
      <div className="settings-page__header">
        <div className="settings-page__head">
          <Typography.Title level={3} className="settings-page__title">
            系统设置
          </Typography.Title>
          <Typography.Text className="settings-page__subtitle">
            模型 / RAG 阈值 / 限流 / 配额配置
          </Typography.Text>
        </div>
      </div>

      <Alert
        className="settings-page__alert"
        type="info"
        showIcon
        message="系统配置当前由环境变量管理，管理后台配置能力待后端开放"
        description="以下配置项当前仅作展示，值为示例/占位，接口开放后将支持在线修改并即时生效。"
      />

      <Form
        form={form}
        layout="vertical"
        disabled
        initialValues={EXAMPLE_VALUES}
        className="settings-form"
      >
        <div className="settings-grid">
          {/* ① 模型配置 */}
          <Card
            className="settings-card"
            title="模型配置"
            extra={<span className="settings-card__tag">仅展示</span>}
          >
            <p className="settings-card__desc">对话模型与采样参数</p>
            <Form.Item label="模型名称" name="model">
              <Select options={MODEL_OPTIONS} />
            </Form.Item>
            <Form.Item label="温度（temperature）" name="temperature" extra="越高回答越随机（0-2）">
              <InputNumber min={0} max={2} step={0.1} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="top_p" name="top_p" extra="核采样概率（0-1）">
              <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
            </Form.Item>
          </Card>

          {/* ② RAG 阈值 */}
          <Card
            className="settings-card"
            title="RAG 阈值"
            extra={<span className="settings-card__tag">仅展示</span>}
          >
            <p className="settings-card__desc">检索召回与答案缓存策略</p>
            <Form.Item label="检索 top_k" name="top_k" extra="每次召回切片数">
              <InputNumber min={1} max={20} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="相似度阈值" name="similarity_threshold" extra="低于该值不召回（0-1）">
              <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="答案缓存开关" name="answer_cache" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Card>

          {/* ③ 限流参数 */}
          <Card
            className="settings-card"
            title="限流参数"
            extra={<span className="settings-card__tag">仅展示</span>}
          >
            <p className="settings-card__desc">接口调用频率控制</p>
            <Form.Item label="每用户每分钟请求上限" name="rpm_limit" extra="超出后返回 429">
              <InputNumber min={1} max={120} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="限流开关" name="rate_limit_enabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Card>

          {/* ④ 配额配置 */}
          <Card
            className="settings-card"
            title="配额配置"
            extra={<span className="settings-card__tag">仅展示</span>}
          >
            <p className="settings-card__desc">用户每日使用额度</p>
            <Form.Item label="每日对话配额" name="daily_quota" extra="单用户每日消息数上限">
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item label="配额提示阈值" name="quota_warn_ratio" extra="使用量达该比例时提醒（0-1）">
              <InputNumber min={0} max={1} step={0.05} style={{ width: '100%' }} />
            </Form.Item>
          </Card>
        </div>

        <div className="settings-actions">
          <Button type="primary" onClick={handleSave}>
            保存配置
          </Button>
          <span className="settings-actions__hint">保存能力待后端配置接口开放后启用</span>
        </div>
      </Form>
    </div>
  );
}

export default SettingsPage;
