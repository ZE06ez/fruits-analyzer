/* BSP 层:GPIO 板级初始化(STEP/DIR 引脚) */
#include "bsp_gpio.h"

void bsp_gpio_init(void)
{
  GPIO_InitTypeDef g = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOC_CLK_ENABLE();

  g.Pin   = STEP_Pin;                 /* PA8:STEP 脉冲输出 */
  g.Mode  = GPIO_MODE_OUTPUT_PP;
  g.Pull  = GPIO_NOPULL;
  g.Speed = GPIO_SPEED_FREQ_VERY_HIGH;
  HAL_GPIO_Init(STEP_GPIO_Port, &g);

  g.Pin   = DIR_Pin;                  /* PC6:DIR 方向输出 */
  g.Mode  = GPIO_MODE_OUTPUT_PP;
  g.Pull  = GPIO_NOPULL;
  g.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(DIR_GPIO_Port, &g);

  bsp_step_set(0);
  bsp_dir_set(1);                     /* 默认正转 */
}

void bsp_step_set(uint8_t level)
{
  HAL_GPIO_WritePin(STEP_GPIO_Port, STEP_Pin, level ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

void bsp_dir_set(uint8_t level)
{
  HAL_GPIO_WritePin(DIR_GPIO_Port, DIR_Pin, level ? GPIO_PIN_SET : GPIO_PIN_RESET);
}
