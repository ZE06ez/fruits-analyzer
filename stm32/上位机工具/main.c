/**
  ******************************************************************************
  * @file    main.c
  * @brief   STM32F407ZGT6 + HBS57H 闭环步进驱动器 —— 匀速慢速旋转测试程序
  *
  *  功能 : 让电机以恒定低速持续匀速旋转,验证接线、驱动器参数和 STM32 脉冲输出。
  *         调试通过后,再改成"发 100 个脉冲停一下"的孔位模式。
  *
  *  引脚 :
  *         PA8  = TIM1_CH1 → STEP 脉冲(复用功能 GPIO_AF1_TIM1)
  *         PA9  = DIR 方向控制(推挽输出)
  *
  *  @note PA9 同时是 USART1_TX。若你的板子 USART1 已被占用(调试串口等),
  *        请把 DIR 换到其他空闲引脚,或把脉冲改到 TIM4_CH1(PB6,普通定时器)。
  *
  *  使用方法 :
  *         1. CubeMX/CubeIDE 新建 STM32F407ZGT6 工程,保留默认时钟配置(HSE);
  *         2. 用本文件整体替换工程生成的 main.c(保留工程自带的 main.h);
  *         3. 编译下载,上电后电机即按 PULSE_HZ 对应速度匀速旋转。
  *
  *  @note 时钟按 HSE = 8MHz、主频 168MHz 配置。若板子晶振是 25MHz,
  *        把 SystemClock_Config() 里的 PLLM 由 8 改为 25,其余不变。
  *
  *  @date  2026-08
  ******************************************************************************
  */

#include "main.h"

/* ============================ 用户配置区 ============================ */
#define PULSE_HZ           400u          /* STEP 脉冲频率(Hz)
                                             1600 脉冲/圈 → 0.25 圈/秒 = 15rpm,慢速稳妥 */
#define PULSE_PER_REV      1600u         /* 驱动器 P-00 细分,须与面板设置一致 */
#define DIR_FORWARD_LEVEL  GPIO_PIN_SET  /* 正转方向电平(1=高电平正转)
                                             若方向反了,改这里,或改驱动器 P-12 */
#define DIR_GPIO_PORT      GPIOA
#define DIR_GPIO_PIN       GPIO_PIN_9
#define AUTO_REVERSE_MS    0u            /* 0 = 恒定一个方向旋转(默认);
                                             >0 = 每隔 N 毫秒反向一次,用于验证 DIR 接线 */
/* ==================================================================== */

TIM_HandleTypeDef htim1;

void SystemClock_Config(void);
static void MX_GPIO_Init(void);
static void MX_TIM1_Init(void);

int main(void)
{
  HAL_Init();
  SystemClock_Config();
  MX_GPIO_Init();
  MX_TIM1_Init();

  /* DIR 先就绪并保持 ≥5us 再发脉冲(HBS57H 手册 t2 要求),
     HAL_Delay 最小 1ms,足够裕量 */
  HAL_GPIO_WritePin(DIR_GPIO_PORT, DIR_GPIO_PIN, DIR_FORWARD_LEVEL);
  HAL_Delay(1);

  /* 启动 TIM1_CH1 PWM 输出 → HBS57H PUL+ */
  HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
  __HAL_TIM_MOE_ENABLE(&htim1);   /* 高级定时器必须开主输出使能,否则 PA8 无输出 */

  while (1)
  {
#if (AUTO_REVERSE_MS > 0)
    HAL_Delay(AUTO_REVERSE_MS);

    /* 换向:先停脉冲 → 改 DIR → 等 1ms → 再发脉冲 */
    HAL_TIM_PWM_Stop(&htim1, TIM_CHANNEL_1);
    HAL_GPIO_TogglePin(DIR_GPIO_PORT, DIR_GPIO_PIN);
    HAL_Delay(1);
    HAL_TIM_PWM_Start(&htim1, TIM_CHANNEL_1);
    __HAL_TIM_MOE_ENABLE(&htim1);
#endif
  }
}

/**
  * @brief  系统时钟配置: HSE 8MHz → PLL → 168MHz
  *         APB1 = 42MHz, APB2 = 84MHz(TIM1 时钟 = 84MHz × 2 = 168MHz)
  */
void SystemClock_Config(void)
{
  RCC_OscInitTypeDef RCC_OscInitStruct = {0};
  RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

  __HAL_RCC_PWR_CLK_ENABLE();
  __HAL_PWR_VOLTAGESCALING_CONFIG(PWR_REGULATOR_VOLTAGE_SCALE1);

  RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSE;
  RCC_OscInitStruct.HSEState       = RCC_HSE_ON;
  RCC_OscInitStruct.PLL.PLLState   = RCC_PLL_ON;
  RCC_OscInitStruct.PLL.PLLSource  = RCC_PLLSOURCE_HSE;
  RCC_OscInitStruct.PLL.PLLM       = 8;        /* 8MHz 晶振: 8/8 = 1MHz */
  RCC_OscInitStruct.PLL.PLLN       = 336;      /* 1MHz × 336 = 336MHz   */
  RCC_OscInitStruct.PLL.PLLP       = RCC_PLLP_DIV2;   /* 336/2 = 168MHz */
  RCC_OscInitStruct.PLL.PLLQ       = 7;
  if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
  {
    Error_Handler();
  }

  RCC_ClkInitStruct.ClockType      = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK
                                   | RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
  RCC_ClkInitStruct.SYSCLKSource   = RCC_SYSCLKSOURCE_PLLCLK;
  RCC_ClkInitStruct.AHBCLKDivider  = RCC_SYSCLK_DIV1;
  RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV4;   /* 42MHz */
  RCC_ClkInitStruct.APB2CLKDivider = RCC_HCLK_DIV2;   /* 84MHz */
  if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_5) != HAL_OK)
  {
    Error_Handler();
  }
}

/**
  * @brief  GPIO 初始化:
  *         PA9  = DIR 方向(推挽输出)
  *         PA8  = TIM1_CH1 复用输出(STEP)
  */
static void MX_GPIO_Init(void)
{
  GPIO_InitTypeDef GPIO_InitStruct = {0};

  __HAL_RCC_GPIOA_CLK_ENABLE();

  /* PA9: DIR 方向 */
  GPIO_InitStruct.Pin   = DIR_GPIO_PIN;
  GPIO_InitStruct.Mode  = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull  = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;   /* 方向信号无需高速 */
  HAL_GPIO_Init(DIR_GPIO_PORT, &GPIO_InitStruct);

  /* PA8: TIM1_CH1 STEP 脉冲 */
  GPIO_InitStruct.Pin       = GPIO_PIN_8;
  GPIO_InitStruct.Mode      = GPIO_MODE_AF_PP;
  GPIO_InitStruct.Pull      = GPIO_NOPULL;
  GPIO_InitStruct.Speed     = GPIO_SPEED_FREQ_VERY_HIGH;
  GPIO_InitStruct.Alternate = GPIO_AF1_TIM1;
  HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);

  /* 初始方向电平 */
  HAL_GPIO_WritePin(DIR_GPIO_PORT, DIR_GPIO_PIN, DIR_FORWARD_LEVEL);
}

/**
  * @brief  TIM1 PWM 初始化:
  *         计数器时钟 168MHz / 84 = 2MHz,自动重装值 = 2MHz / PULSE_HZ,
  *         占空比 50%(脉冲高/低各 1.25ms,远大于手册要求的 2.5us)
  */
static void MX_TIM1_Init(void)
{
  TIM_OC_InitTypeDef sConfigOC = {0};

  htim1.Instance               = TIM1;
  htim1.Init.Prescaler         = 84u - 1u;                    /* 2MHz */
  htim1.Init.CounterMode       = TIM_COUNTERMODE_UP;
  htim1.Init.Period            = (2000000u / PULSE_HZ) - 1u;  /* 400Hz → 4999 */
  htim1.Init.ClockDivision     = TIM_CLOCKDIVISION_DIV1;
  htim1.Init.RepetitionCounter = 0u;
  htim1.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_ENABLE;
  if (HAL_TIM_PWM_Init(&htim1) != HAL_OK)
  {
    Error_Handler();
  }

  sConfigOC.OCMode     = TIM_OCMODE_PWM1;
  sConfigOC.Pulse      = (2000000u / PULSE_HZ) / 2u;   /* 50% 占空比 */
  sConfigOC.OCPolarity = TIM_OCPOLARITY_HIGH;
  sConfigOC.OCFastMode = TIM_OCFAST_DISABLE;
  if (HAL_TIM_PWM_ConfigChannel(&htim1, &sConfigOC, TIM_CHANNEL_1) != HAL_OK)
  {
    Error_Handler();
  }
}
