/* UART output test for STM32MP157 M4
 * Outputs "Hello from M4\r\n" via USART2 (PA2/PA3)
 * Compile: arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -nostdlib -g \
 *          -Wl,-Ttext=0x10000000 -o uart_test.elf uart_test.c
 */

extern unsigned int _estack;
void Reset_Handler(void);

__attribute__((section(".isr_vector")))
void (* const vector_table[])(void) = {
    (void (*)(void))(&_estack),
    Reset_Handler,
};

/* USART2 registers (APB1, offset 0x40004400) */
#define USART2_SR   (*(volatile unsigned int *)0x40004400)
#define USART2_DR   (*(volatile unsigned int *)0x40004404)
#define USART2_BRR  (*(volatile unsigned int *)0x40004408)
#define USART2_CR1  (*(volatile unsigned int *)0x4000440C)

/* GPIOA registers (AHB4, offset 0x50002000) */
#define GPIOA_MODER (*(volatile unsigned int *)0x50002000)
#define GPIOA_AFRL  (*(volatile unsigned int *)0x50002020)

/* RCC registers */
#define RCC_MP_AHB4ENSETR (*(volatile unsigned int *)0x50000A00)
#define RCC_MP_APB1ENSETR (*(volatile unsigned int *)0x50000A08)

static void uart_putc(char c) {
    while (!(USART2_SR & (1 << 7)));  /* Wait for TXE */
    USART2_DR = c;
}

static void uart_puts(const char *s) {
    while (*s) uart_putc(*s++);
}

static void delay(volatile int n) {
    while (n-- > 0);
}

void Reset_Handler(void) {
    __asm__ volatile ("ldr r0, =_estack\n\tmov sp, r0");

    /* Enable GPIOA clock */
    RCC_MP_AHB4ENSETR |= (1 << 0);
    /* Enable USART2 clock */
    RCC_MP_APB1ENSETR |= (1 << 17);

    /* PA2 = AF7 (USART2_TX), PA3 = AF7 (USART2_RX) */
    GPIOA_MODER &= ~((3 << 4) | (3 << 6));
    GPIOA_MODER |= ((2 << 4) | (2 << 6));  /* Alternate function */
    GPIOA_AFRL &= ~((0xF << 8) | (0xF << 12));
    GPIOA_AFRL |= ((7 << 8) | (7 << 12));  /* AF7 */

    /* USART2: 115200 baud, 16MHz HSI */
    USART2_BRR = 16000000 / 115200;
    USART2_CR1 = (1 << 3) | (1 << 0);  /* TE + UE */

    while (1) {
        uart_puts("Hello from M4\r\n");
        delay(1000000);
    }
}
