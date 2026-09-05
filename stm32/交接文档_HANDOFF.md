# HBS57H 电机控制 —— 交接文档(精简版)

> 2026-08-18 | 固件 v1 | 物理验收通过(电机/LED/风扇/推杆全功能)| 含完整引脚图

## 1. 系统与接线(一句话)

STM32F407ZGT6 串口收"角度+转速"命令 → 梯形规划+级联PID → STEP/DIR 脉冲 → DST-1R4P-D 差分 → HBS57H 闭环驱动器(内部闭环保证到位)→ 57 电机带 16 孔滤光片转轮。另控制 YYNMOS-4 四路 PWM(风扇+LED1-3)与 XY-160D 推杆(升降门)。

## 1.1 完整引脚图(全部已用/预留)

| # | STM32 引脚 | 接往 | 功能 |
|---|---|---|---|
| 1 | **PA8** | HBS57H PUL/STEP | 步进脉冲(1600 脉冲/转) |
| 2 | **PC6** | HBS57H DIR | 方向(1=正转) |
| 3 | **PA9** | TTL 模块 RX | USART1 TX,115200-8N1 |
| 4 | **PA10** | TTL 模块 TX | USART1 RX |
| 5 | **PB6** (TIM4_CH1) | YYNMOS-4 PWM1 | 风扇,20kHz |
| 6 | **PB7** (TIM4_CH2) | YYNMOS-4 PWM2 | LED1 |
| 7 | **PB8** (TIM4_CH3) | YYNMOS-4 PWM3 | LED2 |
| 8 | **PB9** (TIM4_CH4) | YYNMOS-4 PWM4 | LED3 |
| 9 | **PC0** | XY-160D ENA1 | 推杆使能(默认低=刹车) |
| 10 | **PC1** | XY-160D IN1 | 推杆方向 |
| 11 | **PC2** | XY-160D IN2 | 推杆方向 |
| 12 | **PC3** (预留) | 开到位限位(常闭+上拉) | 装门后接 |
| 13 | **PC4** (预留) | 关到位限位(常闭+上拉) | 装门后接 |
| 14 | **PA13/PA14** | DAPLink | SWDIO/SWCLK 烧录 |

**共地要求**:上位机 TTL、YYNMOS-4、XY-160D、24V 负极、STM32 GND 全部相连;48V 只接 HBS57H。

```
48V→HBS57H  | 24V→LM2596→5V→DST  | 电脑 COM41(TTL:PA9=TX,PA10=RX,GND共地)  | 24V→YYNMOS-4/XY-160D
STEP=PA8  DIR=PC6(高=正转)  ENA悬空  DAPLink SWD=PA13/PA14  P-00细分=1600
风扇=PB6 LED1=PB7 LED2=PB8 LED3=PB9(YYNMOS-4) 推杆=PC0/PC1/PC2(XY-160D) 限位预留=PC3/PC4
```

## 2. 代码分层(bsp / driver / control)

```
zhunong/
├── Core/            CubeMX 生成(main.c/中断/MSP,勿手改)
├── App/
│   ├── bsp/         板级:bsp_gpio(STEP/DIR引脚) bsp_tim(TIM2脉冲+TIM3节拍) bsp_pwm(TIM4四路:风扇+LED1-3) bsp_door(推杆三脚)
│   ├── driver/      驱动:drv_pulse(TIM2发脉冲/计数/500rpm硬限幅) drv_uart(非阻塞收发)
│   └── control/     控制:ctrl_motor(状态机/规划/PID) ctrl_pid(通用PID) ctrl_protocol(协议) ctrl_door(推杆状态机)
├── zhunong.ioc      引脚/时钟配置(168MHz)
├── 上位机通信协议.md 协议全文档
├── 上位机开发建议.md 上位机开发指南(按采集流程+ACK重试设计)
└── build/Debug/zhunong.hex
```

## 3. 特殊定义值(改前必读)

| 定义 | 值 | 说明 |
|---|---|---|
| `MOTOR_RPM_HARD_LIMIT` | 500 | 速度硬限幅 rpm,命令>500 被拒,三层防护 |
| `MOTOR_PPR_DEFAULT` | 1600 | 每转脉冲,必须与驱动器 P-00 一致 |
| `MOTOR_ACC/DEC_DEFAULT` | **40000 / 60000** | 快速启停:22.5° 约 89ms;500rpm 减速尾段 1000°→~400°。报 Er05 或冲击大则用 SET_PROFILE 调低 |
| PID 默认 | 位置8/0.2/0,速度0.8/8 | 串口 0x04/0x05 实时改 |
| 风扇 PWM | **PB6 = TIM4_CH1,20kHz** | 占空比 0-100%;短帧 `10 01/10 00`(回 `90 00`)或 ASCII `FAN 100`;上电默认关 |
| LED 三路 | **PB7/8/9 = TIM4_CH2/3/4,20kHz** | 位掩码控制:短帧 `12 mask`(bit0-2=LED1-3,回 `92 00`)或 ASCII `LED 7`;0/100% 亮灭,上电默认全灭 |
| 升降门(推杆) | **PC0=ENA1 PC1=IN1 PC2=IN2**(XY-160D 第1路) | 短帧 `11 00`升 `11 01`关 `11 02`停(回 `91 00`)或 ASCII `DOOR 0/1/2`;换向自动刹车100ms;单次连续动作超时28s。2026-09-05 当前机构实测单程约20s,超时仍保留约8s余量 |

## 4. 关键命令

```bash
cd C:\Users\林~\ZCodeProject\HBS57H_STM32
python motor_ctl.py move 90 30      # 绝对 90°@30rpm(二进制协议)
python motor_ctl.py rel 360 100     # 相对 +1圈@100rpm
python motor_ctl.py status / stop / origin
```

**ASCII 文本模式(人工验证,串口助手直接输入,与二进制自动共存):**
上电自动回 `ready`(只一次)→ 输入 `EN` 回 `EN_OK` → `MOVE 22.5 100`(移动22.5°@100rpm)执行完回 `MOVE_OK` → `STOP` 回 `STOP_OK`;未 EN 就 MOVE 回 `MOVE_ERR`。

## 5. 注意事项

- **改动代码后若行为对不上,必须 `rm -rf build` 全新编译**(Windows 日期变更会导致 ninja 陈旧编译)
- 工程用 newlib-nano,scanf 不支持 %f;协议层已用 strtof 手动解析
- 断电重启位置归零 → 对好 1 号孔后发 `origin`
- 运动中收新命令 = BUSY(result=4);>500rpm = result=3
- 短距离(1-2圈)命令 400rpm 实际到不了(三角形曲线),正常
- 接线全程断电;48V 只接驱动器;串口模块必须 3.3V 电平

## 6. 状态

- ✅ 已验证:角度/转速精确控制、500rpm 限幅、100rpm×2min 40轮、CRC 通信、风扇/三路 LED 位掩码协议(短帧/二进制/ASCII)、推杆协议(短帧 11/二进制 0x11/ASCII DOOR,非法参数拒收,换向非阻塞)、电机回归
- ✅ 已修复:脉冲引擎取整卡死、串口 ORE 自愈、TIM4 PWM 通道号映射(1-4→TIM_CHANNEL_x)
- ⚠️ 推杆方向需点动实测:若 `11 01` 不是"关闭",交换推杆红黑线或改 `ctrl_door.h` 中 DOOR_CLOSE_IN1/IN2 定义
- ⚠️ 推杆 `91 00` 仅确认命令接收,不代表已到端点;当前无位置反馈,PC3/PC4 外部限位尚未接入。实测端点处同方向命令无位移且安静,反向可立即移动,判断推杆自身限位有效;仍禁止依赖该现象替代外部安全限位
- ⚠️ 推杆分段“停止→启动”会重置28s超时计时。当前机构分段实测伸出有效运动约19s、缩回约20s,工程记录见 `硬件实测记录_2026-09-05.md`
- ⚠️ DAPLink VCOM 连续突发发送多条短帧后紧跟电机 AA55 MOVE 帧时,现场出现过 MOVE 无 ACK 且电机未启动。上位机必须逐条等待对应 ACK/状态确认,无 ACK 时最多重试3次,不能把“已写串口”当作“已执行”
- ⚠️ LED/风扇 硬件接线后需目视确认亮灭(协议层已通过 STATUS 帧验证)
- 待办:推杆装门后接限位开关(PC3=开到位 PC4=关到位 已预留);400rpm 档耐久复测
