/* Minimal HardFault trigger for STM32MP157 M4
 * Compile: arm-none-eabi-gcc -mcpu=cortex-m4 -mthumb -nostdlib -g -Wl,-Ttext=0x10000000 -o hardfault_test.elf hardfault_test.c
 */

extern unsigned int _estack;

void Reset_Handler(void);
void HardFault_Handler(void);

/* Vector table at 0x10000000 */
__attribute__((section(".isr_vector")))
void (* const vector_table[])(void) = {
    (void (*)(void))(&_estack),     /* Initial SP */
    Reset_Handler,                   /* Reset */
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    HardFault_Handler,               /* HardFault (exception 3) */
};

void HardFault_Handler(void) {
    /* Spin - GDB will catch us here */
    while (1);
}

void Reset_Handler(void) {
    /* Set SP manually */
    __asm__ volatile ("ldr r0, =_estack\n\tmov sp, r0");

    /* Trigger HardFault: write to address 0x0 (NULL pointer dereference) */
    volatile unsigned int *p = (unsigned int *)0x00000000;
    *p = 0xDEADBEEF;

    while (1);
}
