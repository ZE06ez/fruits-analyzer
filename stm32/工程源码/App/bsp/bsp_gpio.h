#ifndef __BSP_GPIO_H
#define __BSP_GPIO_H

#include "main.h"

void bsp_gpio_init(void);            /* STEP(PA8)/DIR(PC6) 输出初始化 */
void bsp_step_set(uint8_t level);    /* STEP 电平:1=高 */
void bsp_dir_set(uint8_t level);     /* DIR 电平:1=正转 */

#endif
