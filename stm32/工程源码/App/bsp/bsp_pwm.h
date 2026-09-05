#ifndef __BSP_PWM_H
#define __BSP_PWM_H

#include "main.h"

/* YYNMOS-4 四路 PWM 驱动(bsp 层,TIM4,20kHz)
   通道:1=风扇(PB6/TIM4_CH1) 2=LED1(PB7/CH2) 3=LED2(PB8/CH3) 4=LED3(PB9/CH4)
   占空比 0~100%:0=关,100=满速/全亮;上电默认全关 */

void    bsp_pwm_init(void);
void    bsp_pwm_set_duty(uint8_t ch, uint8_t pct);   /* ch 1~4 */
uint8_t bsp_pwm_get_duty(uint8_t ch);

#endif
