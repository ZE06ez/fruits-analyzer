/* 驱动层:串口非阻塞收发(USART1)
   TX:环形缓冲,drv_uart_send() 只入队 + 触发发送,实际发送在中断里完成
   RX:drv_uart_isr() 在 USART1 中断里把硬件接收寄存器一次排空到环形缓冲,
      字节到达必入缓冲(不依赖逐字节回调,不丢字节),主循环取走解析 */
#include "drv_uart.h"

UART_HandleTypeDef huart1;         /* 供 stm32f4xx_it.c 使用 */

#define TX_RING_SIZE  256u         /* TX 环形缓冲(2 的幂) */
#define RX_RING_SIZE  256u         /* RX 环形缓冲(2 的幂) */
#define TX_CHUNK_MAX  64u          /* 单次 IT 发送最大字节 */

static volatile uint8_t  s_tx_ring[TX_RING_SIZE];
static volatile uint16_t s_tx_head;             /* 写指针 */
static volatile uint16_t s_tx_tail;             /* 读指针 */
static volatile uint8_t  s_tx_busy;             /* HAL IT 发送进行中 */
static uint8_t  s_tx_chunk[TX_CHUNK_MAX];

static volatile uint8_t  s_rx_ring[RX_RING_SIZE];
static volatile uint16_t s_rx_head;             /* ISR 写 */
static volatile uint16_t s_rx_tail;             /* 主循环读 */

static uint16_t tx_count(void) { return (uint16_t)(s_tx_head - s_tx_tail); }

/* 从 TX 环形取一段交给 HAL 发送;须在关中断下调用 */
static void tx_kick(void)
{
  uint16_t cnt = tx_count();
  if (s_tx_busy || cnt == 0) return;

  uint16_t n = (cnt < TX_CHUNK_MAX) ? cnt : TX_CHUNK_MAX;
  for (uint16_t i = 0; i < n; i++)
    s_tx_chunk[i] = s_tx_ring[(s_tx_tail + i) & (TX_RING_SIZE - 1u)];
  s_tx_tail += n;
  s_tx_busy = 1;
  HAL_UART_Transmit_IT(&huart1, s_tx_chunk, n);
}

void drv_uart_send(const uint8_t *data, uint16_t len)
{
  __disable_irq();
  /* 缓冲放不下则丢弃整帧(不阻塞) */
  if (tx_count() + len <= TX_RING_SIZE - 1u)
  {
    for (uint16_t i = 0; i < len; i++)
      s_tx_ring[(s_tx_head++) & (TX_RING_SIZE - 1u)] = data[i];
    tx_kick();
  }
  __enable_irq();
}

void drv_uart_init(void)
{
  huart1.Instance          = USART1;
  huart1.Init.BaudRate     = 115200;
  huart1.Init.WordLength   = UART_WORDLENGTH_8B;
  huart1.Init.StopBits     = UART_STOPBITS_1;
  huart1.Init.Parity       = UART_PARITY_NONE;
  huart1.Init.Mode         = UART_MODE_TX_RX;
  huart1.Init.HwFlowCtl    = UART_HWCONTROL_NONE;
  huart1.Init.OverSampling = UART_OVERSAMPLING_16;
  if (HAL_UART_Init(&huart1) != HAL_OK) Error_Handler();

  /* 手动使能接收中断:字节到达即进 drv_uart_isr 排空入环形 */
  __HAL_UART_ENABLE_IT(&huart1, UART_IT_RXNE);

  s_tx_head = 0; s_tx_tail = 0; s_tx_busy = 0;
  s_rx_head = 0; s_rx_tail = 0;
}

/* USART1 中断:一次排空硬件接收寄存器入环形(同时清错误标志) */
void drv_uart_isr(void)
{
  /* RXNE:DR 有数据 */
  if (READ_BIT(huart1.Instance->SR, USART_SR_RXNE))
  {
    do
    {
      s_rx_ring[(s_rx_head++) & (RX_RING_SIZE - 1u)] = (uint8_t)huart1.Instance->DR;
    } while (READ_BIT(huart1.Instance->SR, USART_SR_RXNE));
  }
  /* ORE/FE/NE:读 SR 再读 DR 自动清除(DR 已在上方读走) */
  if (READ_BIT(huart1.Instance->SR, USART_SR_ORE | USART_SR_FE | USART_SR_NE))
  {
    (void)huart1.Instance->DR;   /* 清错误标志 */
  }
}

int drv_uart_rx_pop(void)
{
  if (s_rx_tail == s_rx_head) return -1;   /* 空 */
  uint8_t b = s_rx_ring[s_rx_tail & (RX_RING_SIZE - 1u)];
  s_rx_tail++;
  return (int)b;
}

/* 一段发送完成 → 继续取下一段 */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *huart)
{
  if (huart->Instance == USART1)
  {
    __disable_irq();
    s_tx_busy = 0;
    tx_kick();
    __enable_irq();
  }
}
