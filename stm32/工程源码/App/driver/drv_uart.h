#ifndef __DRV_UART_H
#define __DRV_UART_H

#include "main.h"

/* 串口驱动层:非阻塞收发
   TX:环形缓冲 + 中断发送(HAL_UART_Transmit_IT),永不阻塞
   RX:硬件接收中断一次排空到环形缓冲(不丢字节),主循环取走解析 */

void drv_uart_init(void);                        /* USART1 115200-8N1 */
void drv_uart_send(const uint8_t *data, uint16_t len);  /* 非阻塞,可 ISR 内调用 */
void drv_uart_isr(void);                         /* USART1 中断:排空 RXNE 入环形 */
int  drv_uart_rx_pop(void);                      /* 取一个收字节;空返回 -1(主循环调用) */

#endif
