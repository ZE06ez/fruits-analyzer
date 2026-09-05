#ifndef __BSP_TIM_H
#define __BSP_TIM_H

#include "main.h"

extern TIM_HandleTypeDef htim_pulse;   /* TIM2:脉冲引擎(ARR 动态改写) */
extern TIM_HandleTypeDef htim_ctrl;    /* TIM3:1kHz 控制节拍 */

void bsp_tim_init(void);               /* 初始化 TIM2 + TIM3 */

#endif
