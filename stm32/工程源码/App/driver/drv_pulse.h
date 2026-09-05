#ifndef __DRV_PULSE_H
#define __DRV_PULSE_H

#include <stdint.h>

/* 电机脉冲引擎(驱动层):
   TIM2 更新中断翻转 STEP,每 2 个半周期 = 1 个脉冲
   由控制层(ctrl_motor)设定速率/目标,本层负责精确发脉冲与到位停止 */

void    drv_pulse_init(void);
void    drv_pulse_start(int8_t dir);       /* 启动引擎,dir:+1正/-1反 */
void    drv_pulse_stop(void);              /* 立即停,STEP 输出低 */
void    drv_pulse_set_rate(float pps);     /* 设定速率(1kHz 控制环调用) */
float   drv_pulse_get_rate(void);
void    drv_pulse_set_target(int32_t t);   /* 到位停止目标脉冲 */
void    drv_pulse_set_max_pps(uint32_t m); /* 速率硬上限(500rpm 限幅) */
int32_t drv_pulse_get_steps(void);         /* 已发脉冲(带方向) */
void    drv_pulse_isr(void);               /* TIM2 更新中断回调 */

#endif
