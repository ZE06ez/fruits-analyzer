# 两路钨灯网页控制增加说明

## 1. 目的

本文用于指导上位机开发者在现有网页端增加两路钨灯的独立控制入口，并把已经通过实机验证的 STM32 指令安全地接入后端。

本说明基于：

- 仓库：`ZE06ez/fruits-analyzer`
- 当前本地分支：`main`
- 实机控制入口：`host_software/static_ui_prototype_bin/manual_stm32_test.py`
- 串口：DAPLink USB 串口，现场端口为 `COM28`
- 协议：AA55 帧协议，CRC16-CCITT-FALSE
- 实机确认日期：2026-09-11

> 重要：当前硬件把原 RGB LED1、LED2 输出复用为两路钨灯 SSR 控制信号。网页端不能继续把 PB7、PB8 当成普通 RGB LED 通道。

## 2. 当前缺口

当前仓库已经具备以下基础能力：

- `stm32_protocol.py` 中存在灯光控制指令 `led_set_cmd=0x12`。
- `Stm32ControllerAdapter.set_led_mask(mask)` 可以向 STM32 发送灯光位掩码。
- STATUS 能回读 `led_mask`、`led1_duty`、`led2_duty`、`led3_duty`。
- `manual_stm32_test.py --led-mask ...` 已经能够控制 PB7、PB8。
- 页面状态栏已经预留 `tungsten1On`、`tungsten2On` 显示字段。

但网页端目前没有以下真实操作入口：

- 钨灯1开启、关闭。
- 钨灯2开启、关闭。
- 两路钨灯全部关闭。
- 有限时长的安全点亮测试。

现有设备检查页仅提供 LED3 开启/关闭按钮；`deviceTungstenState` 目前只是状态文字，不是可操作开关。

此外，`HardwareController.tungsten_set()` 是旧的高层预留路径，当前 STM32 adapter 仍把 `tungsten_supported` 标记为不支持，不能直接把新按钮接到该旧路径并宣称已经完成适配。

## 3. 当前实机引脚映射

| 实际设备 | STM32 引脚 | 原固件名称 | `led_mask` 位 | 开启参数 |
| --- | --- | --- | --- | --- |
| 钨灯1 SSR | PB7 | LED1 / TIM4_CH2 | bit0 | `0x01` |
| 钨灯2 SSR | PB8 | LED2 / TIM4_CH3 | bit1 | `0x02` |
| 现有 LED3 | PB9 | LED3 / TIM4_CH4 | bit2 | `0x04` |

两路钨灯共用同一条 STM32 指令码，不是两条独立指令码：

```text
CMD = 0x12
PAYLOAD[0] = led_mask
```

常用参数：

| 操作 | 参数 |
| --- | ---: |
| 全部灯光输出关闭 | `0x00` |
| 仅PB7/钨灯1开启 | `0x01` |
| 仅PB8/钨灯2开启 | `0x02` |
| PB7、PB8同时开启 | `0x03` |
| 仅PB9/LED3开启 | `0x04` |

## 4. 位掩码更新要求

网页端不能把每个开关简单实现为“开启时发送固定值，关闭时发送0”，否则操作某一路会误关其他通道。

普通单路操作必须先取得当前新鲜 STATUS，再按位修改：

```python
TUNGSTEN_1_BIT = 0x01  # PB7
TUNGSTEN_2_BIT = 0x02  # PB8
LED3_BIT = 0x04        # PB9

def update_channel(current_mask: int, bit: int, enabled: bool) -> int:
    if enabled:
        return current_mask | bit
    return current_mask & ~bit
```

要求：

- 钨灯1开启：`new_mask = current_mask | 0x01`。
- 钨灯1关闭：`new_mask = current_mask & ~0x01`。
- 钨灯2开启：`new_mask = current_mask | 0x02`。
- 钨灯2关闭：`new_mask = current_mask & ~0x02`。
- 普通“两路钨灯关闭”：`new_mask = current_mask & 0x04`，保留 PB9/LED3 状态。
- 紧急“全部光源关闭”：直接发送 `0x00`，不保留任何通道。

现有 `DeviceManager.set_led3()` 使用 `0x04` 或 `0x00` 覆盖整个 mask。增加钨灯按钮时必须同步改成按位更新，否则操作 LED3 会误关 PB7、PB8，操作钨灯也可能误关 LED3。

## 5. 建议增加的网页控件

建议在“设备检查”或“光源检测”页面增加独立的“钨灯手动测试”区域：

### 钨灯1（PB7）

- `点亮5秒`按钮。
- `立即关闭`按钮。
- 状态显示：关闭、开启中、关闭失败、状态未知。
- 显示回读值：`led1_duty`。

### 钨灯2（PB8）

- `点亮5秒`按钮。
- `立即关闭`按钮。
- 状态显示：关闭、开启中、关闭失败、状态未知。
- 显示回读值：`led2_duty`。

### 公共控制

- 一个醒目的`全部钨灯关闭`按钮。
- 一个倒计时显示，例如“钨灯1将在4.2秒后关闭”。
- 显示当前串口、最近一次新鲜 STATUS 时间和故障码。
- 在安全能力完成前，不提供“永久开启”按钮。
- 在电源容量和冷态冲击电流未验收前，不开放“两路同时开启”。

按钮不得只写前端日志；必须调用后端 API，并依据命令 ACK 和新鲜 STATUS 更新结果。

## 6. 后端 API 建议

新增：

```text
POST /api/device/tungsten
POST /api/device/tungsten/all-off
```

单路有限时点亮请求：

```json
{
  "channel": 1,
  "enabled": true,
  "durationMs": 5000
}
```

单路立即关闭请求：

```json
{
  "channel": 2,
  "enabled": false
}
```

返回至少包含：

```json
{
  "ok": true,
  "commandAccepted": true,
  "channel": 1,
  "enabled": true,
  "durationMs": 5000,
  "autoOffScheduled": true,
  "requestedMask": 1,
  "confirmedMask": 1,
  "statusFresh": true,
  "errorCode": 0,
  "message": "钨灯1已开启，将在5秒后自动关闭"
}
```

所有 API 必须复用 `DeviceManager` 持有的同一个串口连接。网页后端不得启动 `manual_stm32_test.py` 子进程，否则可能抢占 COM28。

## 7. 后端执行流程

### 7.1 开启流程

1. 确认 STM32 已连接。
2. 确认没有急停或未清除故障。
3. 确认防护罩/箱体已经关闭；没有可靠门控信号时要求人工确认。
4. 确认散热风扇已经开启并得到新鲜 STATUS 回读。
5. 确认 PB9/LED3 已关闭；若产品要求两类光源互斥，应拒绝而不是静默覆盖。
6. 读取命令前的新鲜 `led_mask`。
7. 按位计算目标 mask。
8. 调用 `adapter.set_led_mask(target_mask)`。
9. 收到匹配的 ACK 后等待命令之后的新鲜 STATUS。
10. 检查目标通道 duty 是否为100，且 `error_code=0`。
11. 在后端启动自动关闭计时器。
12. 到时后发送按位关闭命令并再次验证新鲜 STATUS。

### 7.2 关闭流程

关闭属于安全动作，不得被“风扇未开启”“门未关闭”等普通联锁阻止。

1. 读取当前 mask；读取失败时仍应尝试发送关闭命令。
2. 单路关闭按位清零。
3. 紧急全部关闭直接发送 `0x00`。
4. 等待 ACK 和新鲜 STATUS。
5. 若无法确认关闭，页面必须显示红色高优先级提示：`无法确认钨灯已关闭，请立即切断12V光源电源`。

## 8. 强制安全要求

本节属于必须实现项，不是可选优化。

### 8.1 禁止自动点亮

以下行为都不得自动点亮PB7或PB8：

- 页面载入。
- 串口连接。
- 读取状态。
- 设备扫描。
- 硬件通信自检。
- 串口重连。
- 软件启动或崩溃恢复。

### 8.2 人工确认

每次手动点亮前必须明确确认：

- 两只灯已经固定安装。
- 照射方向内无人眼、皮肤和易燃物暴露。
- 防护箱体或遮光罩已经关闭。
- 12V负载线路、SSR及保险丝安装可靠。
- 操作员可以立即接触到12V总电源开关。

### 8.3 自动关闭

- 手动测试默认持续时间为5秒。
- 后端限制手动测试时长，建议范围为1～5秒。
- 前端关闭、浏览器崩溃或网络断开不能取消后端自动关闭任务。
- 自动关闭失败时只允许重试关闭，不允许重试开启。
- 串口断开或 STATUS 超时应立即尝试发送关闭，并提示人工切断12V。

### 8.4 硬件失效保护

仅依赖网页定时器不能满足最终设备安全要求。正式运行前建议增加：

- PB7、PB8控制端硬件下拉，STM32复位和掉电时SSR默认关闭。
- 独立的12V光源总电源开关或急停。
- 防护门联锁直接切断钨灯12V负载电源，不能只依赖上位机串口。
- 固件钨灯看门狗/租约：超过规定时间未收到有效保活或关闭命令时自动关闭。
- 每一路负载独立保险丝。
- 验证12V电源能够承受钨丝灯冷态冲击电流后，才允许两路同时开启。

## 9. 实机观察与风险记录

2026-09-11实机测试确认：

- PB7通过 `led_mask=0x01`能够驱动第一路SSR及钨灯。
- PB8通过 `led_mask=0x02`能够驱动第二路SSR及钨灯。
- STM32能够回读PB7、PB8占空比100和关闭后的0。
- 测试过程中出现过PB8点亮后 STATUS 暂时停止返回，常规脚本因握手失败而无法立即确认关闭；随后通过直接发送 `set_led_mask(0)`取得ACK，并再次回读确认 `led_mask=0`。
- 曾出现灯光照射到未确认安全的区域，操作员手动切断12V电源。

因此，上位机不得把“串口命令已发送”当作“光源一定安全关闭”，必须保留物理断电手段，并将关闭失败提示置于最高优先级。

## 10. 自动化测试要求

至少补充以下测试：

- PB7、PB8通道到bit0、bit1的映射。
- 开启或关闭一路时保留另一通道状态。
- 操作LED3时不覆盖钨灯位。
- `all-off`始终发送 `0x00`。
- 未连接时拒绝开启，但允许返回明确的关闭失败提示。
- 风扇关闭、门未确认、LED3开启时拒绝开启钨灯。
- 开启后达到时限自动关闭。
- 前端离开页面后后端计时器仍然执行。
- ACK成功但没有新鲜 STATUS 时不显示“已确认开启/关闭”。
- STATUS超时后触发关闭尝试及人工断电警告。
- 连接、自检、刷新页面和读取状态不会发送开启命令。
- 两路同时开启在能力未验收时被拒绝。

## 11. 验收标准

- 网页能够独立、有限时长地控制PB7和PB8。
- 两路关闭按钮和“全部钨灯关闭”可用。
- 页面显示值与STM32新鲜 STATUS 一致。
- 任何连接、自检或重连流程都不会自动点亮钨灯。
- 操作一路不会误改变另一路或PB9状态。
- 串口异常时页面不会误报已经安全关闭。
- 自动关闭失败时给出明确的物理断电提示。
- 两路同时开启必须在电源、散热和防护联锁验收后才能启用。
- 实机验收结束时确认 `led_mask=0`、`led1_duty=0`、`led2_duty=0`。

## 12. 相关代码位置

- `host_software/static_ui_prototype_bin/stm32_protocol.py`
- `host_software/static_ui_prototype_bin/stm32_controller.py`
- `host_software/static_ui_prototype_bin/device_manager.py`
- `host_software/static_ui_prototype_bin/hardware_controller.py`
- `host_software/static_ui_prototype_bin/backend_server.py`
- `host_software/static_ui_prototype_bin/index.html`
- `host_software/static_ui_prototype_bin/app.js`
- `host_software/static_ui_prototype_bin/manual_stm32_test.py`

现有 `docs/UPPER_COMPUTER_STM32_INTEGRATION_REQUEST.md` 中“钨灯控制当前不可用”的结论需要在合入本适配后更新为：底层PB7/PB8复用控制已实机验证，上位机专用按钮、安全联锁和最终验收仍待完成。
