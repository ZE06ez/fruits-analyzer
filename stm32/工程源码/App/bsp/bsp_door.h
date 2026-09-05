#ifndef __BSP_DOOR_H
#define __BSP_DOOR_H

#include "main.h"

/* BSP 层:XY-160D 双路H桥 第1路(24V 电动推杆/升降门)
   引脚:DOOR_EN=PC0→ENA1, DOOR_IN1=PC1→IN1, DOOR_IN2=PC2→IN2
   真值表:IN1=IN2=0 刹车;ENA=1 且 IN1/IN2=10 全速正转,01 全速反转
   上电默认刹车(ENA=0),确保启动不动作 */

void bsp_door_init(void);                          /* 三脚推挽输出,默认刹车 */
void bsp_door_brake(void);                         /* 刹车:ENA=0,IN1=IN2=0 */
void bsp_door_drive(uint8_t in1, uint8_t in2);     /* 设方向并 ENA=1(全速) */
uint8_t bsp_door_is_braked(void);                  /* 当前是否刹车 */

#endif
