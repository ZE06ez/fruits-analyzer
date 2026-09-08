# 上位机 STM32 真实控制接入与问题修复要求

## 1. 任务背景

请基于以下代码版本补齐网页端硬件调试能力：

- 仓库：`ZE06ez/fruits-analyzer`
- 分支：`pb-3.7-camera-device-selection`
- 基准提交：`95bf984`（`Bind current STM32 firmware profile`）
- STM32 实机测试入口：`host_software/static_ui_prototype_bin/manual_stm32_test.py`
- 实机测试记录：`docs/HARDWARE_TEST_LOG.md`
- 现场串口：DAPLink USB 串口 `COM28`，115200 8N1

当前 `manual_stm32_test.py` 使用的 `SerialService`、`Stm32ControllerAdapter` 和 `CURRENT_STM32_FIRMWARE_PROFILE` 已能通过 AA55 协议真实控制 STM32。当前主要缺口不是重新编写 STM32 协议，而是网页按钮、后端单项控制 API、状态确认和安全保护尚未完整接入。

## 2. 已完成的实机验证

### 2.1 STM32 通信

- COM28 可以正常打开。
- AA55 握手成功。
- `currentFirmwareProfileValidated=true`。
- STATUS 可以读取电机位置、目标位置、运动状态、风扇、LED 和故障码。
- 本轮测试故障码均为 `0`。

### 2.2 风扇

底层调用：

```python
adapter.set_fan(True)
adapter.set_fan(False)
```

实机结果：

- 开启后：`fan_on=true`、`fan_duty=100`，风扇实际转动。
- 关闭后：`fan_on=false`、`fan_duty=0`，风扇实际停止。

### 2.3 LED3

底层调用：

```python
adapter.set_led_mask(0x04)  # LED3 开启
adapter.set_led_mask(0x00)  # 全部 LED 关闭
```

实机结果：

- 开启后：`led_mask=4`、`led3_duty=100`，LED3 实际点亮。
- 关闭后：`led_mask=0`、`led3_duty=0`，LED3 实际熄灭。
- 重复测试结果稳定。

### 2.4 推杆电机

现场验证发现，实际伸缩方向与当前 `door open/close` 命名相反：

| 操作员语义 | 当前底层调用 | 原始动作值 | 实机结果 |
| --- | --- | --- | --- |
| 推杆伸出 | `adapter.door_command(0x01)` | 当前代码称 `close` | 实际向外伸出 |
| 推杆缩回 | `adapter.door_command(0x00)` | 当前代码称 `open` | 实际向内缩回 |
| 推杆停止 | `adapter.door_command(0x02)` | `stop` | 实际停止 |

启动和停止命令均能收到 ACK，故障码为 `0`。当前固件不提供推杆位置、端点或限位反馈，因此上位机不能把 ACK 当作“已经伸到位/缩到位”。

### 2.5 滤光轮步进电机

当前实机参数：

- 16 个槽位。
- 每格 `22.5°`。
- 本轮测试默认速度 `10 RPM`。

方向映射：

| 操作员语义 | 槽位增量 | 实机方向 |
| --- | ---: | --- |
| 逆时针 | 正值，例如 `+1`、`+2` | 逆时针 |
| 顺时针 | 负值，例如 `-1`、`-2` | 顺时针 |

已确认位置变化：

- `0° -> 22.5°`：正增量一格，通过。
- `22.5° -> 45°`：正增量一格，通过。
- `45° -> 90°`：正增量两格，通过。
- `90° -> 45°`：负增量两格，实物顺时针，通过。
- `45° -> 67.5° -> 112.5°`：正增量一格、两格，实物逆时针，通过。

## 3. 已发现的问题

### 3.1 网页按钮未接入真实控制

网页中的以下按钮目前只有 `data-log`，点击后只写日志，不调用后端，也不向 STM32 发送命令：

- 平台正转
- 平台反转
- 升降复位

网页还缺少以下真实控制入口：

- 风扇关闭。
- LED3 开启/关闭。
- 推杆伸出/缩回/停止。
- 步进电机顺时针/逆时针/停止。
- 步进电机格数或角度设置。

### 3.2 步进电机存在 ACK 成功但未运动的情况

现场出现过一次：

```text
命令：move-rel-slots +1
起始位置：45°
预期位置：67.5°
ACK：成功
故障码：0
最终位置：45°
目标位置：45°
实物观察：没有运动
```

随后发送正增量两格可以正常从 `45°` 到达 `90°`。因此该问题具有间歇性，不能简单认定为命令格式错误。

当前 `manual_stm32_test.py` 会在调用返回后直接打印 `moveRelSlots`。当前 adapter 可能接受命令前缓存的 `done` STATUS，或者在等待超时后返回最后一次状态，造成误报成功。

### 3.3 推杆方向语义相反

当前 `door open/close` 命名不能直接用于网页：

- `door open / 0x00` 实际是推杆缩回。
- `door close / 0x01` 实际是推杆伸出。

网页必须显示操作员能够理解的“推杆伸出/缩回/停止”，并在上位机适配层集中完成映射。

### 3.4 硬件通信自检会让风扇持续开启

当前 `DeviceManager.self_test()` 执行 PING 后调用 `fan_on()`，但没有在测试结束后关闭风扇。因此点击“硬件通信自检”后风扇会持续运转。

需要明确产品策略：

- 若只是风扇测试，应运行有限时间后恢复测试前状态；或
- 明确提示“风扇将保持开启”，并提供可见的关闭按钮。

### 3.5 当前固件不支持自动 HOME

当前 STM32 profile 明确：

- `automaticHoming=false`
- `physicalEncoderVerified=false`

网页不应把“滤光轮寻零自检”显示成可用的真实自动寻零功能。若需要设置逻辑零点，应明确要求操作员先人工对准，再执行 `SET_ORIGIN`；不能把 `SET_ORIGIN` 描述成自动寻找 HOME 传感器。

### 3.6 当前不支持的能力

- 钨灯控制当前不可用。
- 推杆端点/限位反馈当前不可用。
- CL57C 物理编码器反馈尚未验证。
- 独立样品旋转台没有真实 STM32 控制接口。

这些能力不得在网页中显示为已通过或真实可用。

## 4. 网页端需要增加的控制区

建议在现有“电机检测/设备检查”页面内增加真实硬件调试区，不需要重新设计整个 UI。

### 4.1 风扇

- `开启风扇`
- `关闭风扇`
- 显示 `fan_on` 和 `fan_duty`

### 4.2 LED3

- `开启 LED3`
- `关闭 LED3`
- 显示 `led_mask` 和 `led3_duty`

当前现场只使用 LED3，不要默认同时点亮 LED1/LED2。

### 4.3 推杆电机

- `伸出`
- `缩回`
- `停止`
- 动作时长设置，例如 `0.5/1/2/5 秒`
- 到时自动发送停止命令

实际映射：

```text
伸出 -> door_command(0x01)
缩回 -> door_command(0x00)
停止 -> door_command(0x02)
```

由于没有端点反馈，页面必须显示“命令已接受，不代表已经到达限位”。

### 4.4 滤光轮步进电机

- `顺时针`
- `逆时针`
- `停止`
- 转动格数输入，建议现场调试默认 `1` 格
- 显示每格 `22.5°`
- 显示起始位置、目标位置、最终位置、运动状态和故障码

实际映射：

```text
逆时针 -> move_filter_wheel_relative_slots(+slots)
顺时针 -> move_filter_wheel_relative_slots(-slots)
停止   -> stop_filter_wheel()
```

不要直接向普通操作员展示正负号。

### 4.5 通用控制

- 读取状态。
- 紧急停止。
- 清除故障。
- 操作日志。
- 当前串口和连接状态。
- 命令执行期间的忙碌状态。

## 5. 后端 API 建议

所有 API 必须复用当前 `DeviceManager` 持有的同一个串口连接，不得从网页启动 `manual_stm32_test.py`，否则会与网页抢占 COM28。

建议增加：

```text
POST /api/device/fan
POST /api/device/led
POST /api/device/actuator
POST /api/device/wheel/move-relative
POST /api/device/wheel/stop
```

参考请求体：

```json
{"enabled": true}
```

```json
{"channel": 3, "enabled": true}
```

```json
{"action": "extend", "durationMs": 1000}
```

```json
{"direction": "clockwise", "slots": 1}
```

API 返回至少应包含：

```json
{
  "ok": true,
  "ackReceived": true,
  "command": "wheel_move_relative",
  "statusBefore": {},
  "expectedTarget": 0.0,
  "statusAfter": {},
  "errorCode": 0,
  "message": ""
}
```

## 6. 运动结果判定要求

机械命令不能只根据 ACK 判断成功。

步进电机运动成功必须同时满足：

1. 收到与本次命令匹配的 ACK。
2. 收到命令发送之后的新 STATUS。
3. STATUS 中位置或目标位置相对命令前发生预期变化。
4. 实际位置进入目标位置容差。
5. 电机最终回到 `done` 或 `idle`。
6. `error_code=0`。

若超时、位置未变化、目标不匹配或出现故障：

1. 显示明确失败，不得显示“已完成”。
2. 发送步进电机 STOP。
3. 禁止盲目自动重发机械命令。
4. 保留起始位置、预期目标、最终位置、ACK 和原始错误信息供排查。

## 7. 安全与交互要求

- STM32 未连接时禁用所有真实控制按钮。
- 命令执行期间禁用同类按钮，防止重复点击。
- 推杆必须提供独立停止按钮和自动停止保护。
- 步进电机必须提供独立停止按钮。
- 页面必须区分“命令已接受”和“动作已完成”。
- 不得把占位日志显示成真实硬件执行成功。
- 不得把 `SET_ORIGIN` 描述为自动寻零。
- 保留现有急停和故障清除功能。
- 急停后的输出策略应明确提示；当前 `safe_stop()` 会保持风扇开启。

## 8. 测试要求

请为新增功能补充自动化测试：

- 后端 API 参数校验和未连接处理。
- 风扇、LED3、推杆、步进电机命令映射。
- 推杆动作超时后自动停止。
- 步进电机正负方向映射。
- ACK 成功但位置不变时必须判定失败。
- 旧 STATUS 不能作为本次运动完成依据。
- 运动超时后发送 STOP。
- 前端按钮不再只有 `data-log`，而是调用真实 API。
- 串口连接只由一个 `DeviceManager` 持有。

测试完成后运行：

```bat
cd /d D:\fruits-analyzer-main\fruits-analyzer-main\host_software\static_ui_prototype_bin
python -X utf8 -m unittest discover -s tests -v
```

## 9. 验收标准

- 操作员可以在网页中独立控制风扇、LED3、推杆和滤光轮步进电机。
- 网页显示的方向和动作名称与现场实物一致。
- 推杆伸出/缩回映射正确，并能可靠自动停止。
- 步进电机正增量显示为逆时针，负增量显示为顺时针。
- 运动命令不会仅因收到 ACK 就误报完成。
- 不支持的自动寻零、钨灯、推杆限位反馈不会显示为可用。
- 页面日志能区分命令已发送、ACK 已收到、运动完成和运动失败。
- 不启动第二个进程抢占 COM28。
