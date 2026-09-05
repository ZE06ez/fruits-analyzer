/* BSP 层:YYNMOS-4 四路 PWM(TIM4,20kHz)
   通道1=风扇,通道2/3/4=LED1/2/3;上电默认全关 */
#include "bsp_pwm.h"

#define PWM_TIM_CLK  84000000UL   /* TIM4 时钟 = APB1(84MHz) */
#define PWM_HZ       20000u       /* PWM 频率 20kHz */

static TIM_HandleTypeDef htim_pwm;
static uint8_t s_duty[4];         /* 各通道占空比 */

/* 对外 ch(1~4) → HAL TIM_CHANNEL_x(0x0000/0x0004/0x0008/0x000C) */
static const uint32_t s_ch_map[4] = { TIM_CHANNEL_1, TIM_CHANNEL_2, TIM_CHANNEL_3, TIM_CHANNEL_4 };

void bsp_pwm_init(void)
{
  TIM_OC_InitTypeDef oc = {0};

  htim_pwm.Instance               = TIM4;
  htim_pwm.Init.Prescaler         = 3u;                 /* 84M/4 = 21MHz */
  htim_pwm.Init.CounterMode       = TIM_COUNTERMODE_UP;
  htim_pwm.Init.Period            = 1049u;              /* 21M/1050 = 20kHz */
  htim_pwm.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
  htim_pwm.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_PWM_Init(&htim_pwm) != HAL_OK) Error_Handler();

  oc.OCMode     = TIM_OCMODE_PWM1;
  oc.Pulse      = 0u;              /* 默认全关 */
  oc.OCPolarity = TIM_OCPOLARITY_HIGH;
  oc.OCFastMode = TIM_OCFAST_DISABLE;
  for (uint8_t i = 0u; i < 4u; i++)
  {
    if (HAL_TIM_PWM_ConfigChannel(&htim_pwm, &oc, s_ch_map[i]) != HAL_OK) Error_Handler();
    HAL_TIM_PWM_Start(&htim_pwm, s_ch_map[i]);
  }
  for (uint8_t i = 0u; i < 4u; i++) s_duty[i] = 0u;
}

void bsp_pwm_set_duty(uint8_t ch, uint8_t pct)
{
  if (ch < 1u || ch > 4u) return;
  if (pct > 100u) pct = 100u;
  s_duty[ch - 1u] = pct;
  __HAL_TIM_SET_COMPARE(&htim_pwm, s_ch_map[ch - 1u],
                        (uint32_t)pct * htim_pwm.Init.Period / 100u);
}

uint8_t bsp_pwm_get_duty(uint8_t ch)
{
  if (ch < 1u || ch > 4u) return 0u;
  return s_duty[ch - 1u];
}
