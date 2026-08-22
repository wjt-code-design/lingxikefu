import { describe, it, expect } from 'vitest';
import { detectOrderTrack } from './orderTrack';

/** 覆盖典型订单轨迹格式：
 *  - 多订单并列（你之前的 agent 输出格式）
 *  - 订单尾号 + 订单号混合
 *  - 状态关键词：已发货 / 派送中 / 已签收 / 待送装
 *  - [来源N] 引用应当被剥离
 *  - 尾部自然语言提示保留
 */
describe('detectOrderTrack', () => {
  it('识别多订单并列（标准格式）', () => {
    const input = [
      '资料中有以下订单轨迹：',
      '- 订单 SO2026080141：即热饮水机 W7 - 已发货，预计 2026-08-16 前（同城次日达）[来源1]',
      '- 订单 SO2026080118：空气净化器 K1 - 已发货，最新派送中，预计 2026-08-13 18:00 前送达 [来源4]',
      '请提供您的订单号，我为您查询最新状态。',
    ].join('\n');
    const r = detectOrderTrack(input);
    expect(r.detected).toBe(true);
    expect(r.items.length).toBe(2);
    expect(r.items[0].orderNo).toBe('SO2026080141');
    expect(r.items[0].product).toContain('即热饮水机');
    expect(r.items[0].status).toBe('已发货');
    expect(r.items[0].detail).toContain('预计');
    // 顾客端来源引用被剥离
    expect(input).toContain('[来源1]');
    expect(r.items[0].raw).not.toMatch(/\[来源\d+\]/);
    // 尾部自然语言保留
    expect(r.footer).toMatch(/请提供您的订单号/);
  });

  it('识别"订单尾号 XX"变体', () => {
    const input = '- 订单尾号 8823：洗衣机（订单号 SO2026080199）- 显示已签收但用户未收到 [来源1]';
    const r = detectOrderTrack(input);
    expect(r.detected).toBe(true);
    expect(r.items.length).toBe(1);
    expect(r.items[0].orderNo).toBe('SO2026080199');
    expect(r.items[0].status).toBe('已签收');
    expect(r.items[0].raw).not.toMatch(/\[来源\d+\]/);
  });

  it('没有订单关键词时不误报', () => {
    const input = '- 这是一个普通列表\n- 没有订单信息';
    const r = detectOrderTrack(input);
    expect(r.detected).toBe(false);
  });

  it('流式半成品（只有一行订单 + 未完成列表）也可识别', () => {
    const input = '资料中有以下订单轨迹：\n- 订单 SO2026080141：即热饮水机 W7 - 已发货';
    const r = detectOrderTrack(input);
    expect(r.detected).toBe(true);
    expect(r.items.length).toBe(1);
    expect(r.items[0].status).toBe('已发货');
  });

  it('待送装状态解析', () => {
    const input = '- 订单 SO2026080091：滚筒洗衣机 W5 - 待送装状态，预约时间 2026-08-16 14:00-17:00 [来源2]';
    const r = detectOrderTrack(input);
    expect(r.detected).toBe(true);
    expect(r.items[0].status).toBe('待送装');
    expect(r.items[0].detail).toMatch(/预约时间/);
  });
});
