/* BSP 层:XY-160D 双路H桥 第1路 GPIO 控制(推杆/升降门)
   改接线只需改下面三行引脚定义 */
#include "bsp_door.h"

#define DOOR_EN_PORT    GPIOC          /* ENA1 */
#define DOOR_EN_PIN     GPIO_PIN_0
#define DOOR_IN1_PORT   GPIOC          /* IN1 */
#define DOOR_IN1_PIN    GPIO_PIN_1
#define DOOR_IN2_PORT   GPIOC          /* IN2 */
#define DOOR_IN2_PIN    GPIO_PIN_2

void bsp_door_init(void)
{
  GPIO_InitTypeDef g = {0};

  __HAL_RCC_GPIOC_CLK_ENABLE();

  g.Pin   = DOOR_EN_PIN | DOOR_IN1_PIN | DOOR_IN2_PIN;
  g.Mode  = GPIO_MODE_OUTPUT_PP;
  g.Pull  = GPIO_NOPULL;
  g.Speed = GPIO_SPEED_FREQ_LOW;       /* 慢信号,普通推杆控制 */
  HAL_GPIO_Init(GPIOC, &g);

  bsp_door_brake();                    /* 上电安全:刹车不动作 */
}

void bsp_door_brake(void)
{
  HAL_GPIO_WritePin(DOOR_EN_PORT,  DOOR_EN_PIN,  GPIO_PIN_RESET);
  HAL_GPIO_WritePin(DOOR_IN1_PORT, DOOR_IN1_PIN, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(DOOR_IN2_PORT, DOOR_IN2_PIN, GPIO_PIN_RESET);
}

void bsp_door_drive(uint8_t in1, uint8_t in2)
{
  HAL_GPIO_WritePin(DOOR_IN1_PORT, DOOR_IN1_PIN, in1 ? GPIO_PIN_SET : GPIO_PIN_RESET);
  HAL_GPIO_WritePin(DOOR_IN2_PORT, DOOR_IN2_PIN, in2 ? GPIO_PIN_SET : GPIO_PIN_RESET);
  HAL_GPIO_WritePin(DOOR_EN_PORT,  DOOR_EN_PIN,  GPIO_PIN_SET);
}

uint8_t bsp_door_is_braked(void)
{
  return (HAL_GPIO_ReadPin(DOOR_EN_PORT, DOOR_EN_PIN) == GPIO_PIN_RESET);
}
