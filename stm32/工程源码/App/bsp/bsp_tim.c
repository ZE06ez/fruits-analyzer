/* BSP 层:定时器板级初始化 + 更新中断分发
   TIM2=脉冲引擎(TIM_IT_UPDATE,ARR 由驱动层动态改写)
   TIM3=1kHz 控制节拍 */
#include "bsp_tim.h"
#include "../driver/drv_pulse.h"
#include "../control/ctrl_motor.h"

TIM_HandleTypeDef htim_pulse;
TIM_HandleTypeDef htim_ctrl;

static void tim_pulse_init(void)   /* TIM2: 84MHz 满速计数,32位 */
{
  htim_pulse.Instance               = TIM2;
  htim_pulse.Init.Prescaler         = 0;
  htim_pulse.Init.CounterMode       = TIM_COUNTERMODE_UP;
  htim_pulse.Init.Period            = 0xFFFFFFFFu;               /* ARR 动态改写 */
  htim_pulse.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
  htim_pulse.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim_pulse) != HAL_OK) Error_Handler();
}

static void tim_ctrl_init(void)    /* TIM3: 1MHz 计数,1kHz 中断 */
{
  htim_ctrl.Instance               = TIM3;
  htim_ctrl.Init.Prescaler         = 83;                          /* 84MHz/84=1MHz */
  htim_ctrl.Init.CounterMode       = TIM_COUNTERMODE_UP;
  htim_ctrl.Init.Period            = 999;                         /* 1kHz */
  htim_ctrl.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
  htim_ctrl.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_Base_Init(&htim_ctrl) != HAL_OK) Error_Handler();
}

void bsp_tim_init(void)
{
  tim_pulse_init();
  tim_ctrl_init();
}

/* HAL 定时器更新中断回调(时钟使能/NVIC 在 hal_msp.c) */
void HAL_TIM_PeriodElapsedCallback(TIM_HandleTypeDef *htim)
{
  if (htim->Instance == TIM2)      drv_pulse_isr();        /* 发脉冲 */
  else if (htim->Instance == TIM3) ctrl_motor_tick_1khz(); /* 控制环 */
}
