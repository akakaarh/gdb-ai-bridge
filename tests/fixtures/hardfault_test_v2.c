#include <stdint.h>

#define STACK_TOP   0x10040000UL
#define SCB_BASE    0xE000ED00UL
#define SCB_CFSR    (*(volatile uint32_t *)(SCB_BASE + 0x28))
#define SCB_HFSR    (*(volatile uint32_t *)(SCB_BASE + 0x2C))
#define SCB_MMFAR   (*(volatile uint32_t *)(SCB_BASE + 0x34))
#define SCB_BFAR    (*(volatile uint32_t *)(SCB_BASE + 0x38))

volatile uint32_t crash_r0, crash_r1, crash_r2, crash_r3;
volatile uint32_t crash_r12, crash_lr, crash_pc, crash_xpsr;
volatile uint32_t crash_cfsr, crash_hfsr, crash_mmfar, crash_bfar;
volatile uint32_t crash_sp;
const char *fault_type_str = "unknown";

void Reset_Handler(void);
void HardFault_Handler(void);

__attribute__((section(".isr_vector")))
void (* const vector_table[])(void) = {
    (void (*)(void))(STACK_TOP),
    Reset_Handler, 0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,
    HardFault_Handler,
};

__attribute__((naked))
void HardFault_Handler(void) {
    /* Always use MSP — we're in bare-metal Handler mode */
    __asm__ volatile (
        "mrs r0, msp        \n"
        "b hardfault_c      \n"
    );
}

void hardfault_c(uint32_t *frame) {
    crash_r0    = frame[0];
    crash_r1    = frame[1];
    crash_r2    = frame[2];
    crash_r3    = frame[3];
    crash_r12   = frame[4];
    crash_lr    = frame[5];
    crash_pc    = frame[6];
    crash_xpsr  = frame[7];
    crash_sp    = (uint32_t)frame;
    crash_cfsr  = SCB_CFSR;
    crash_hfsr  = SCB_HFSR;
    crash_mmfar = SCB_MMFAR;
    crash_bfar  = SCB_BFAR;

    if (crash_cfsr & (1 << 1))       fault_type_str = "DACCVIOL";
    else if (crash_cfsr & (1 << 0))  fault_type_str = "IACCVIOL";
    else if (crash_cfsr & (1 << 9))  fault_type_str = "PRECISERR";
    else if (crash_cfsr & (1 << 25)) fault_type_str = "UNDEFINSTR";
    else if (crash_cfsr & (1 << 26)) fault_type_str = "INVSTATE";

    while (1);
}

void Reset_Handler(void) {
    __asm__ volatile (
        "ldr r0, =0x10040000\n\t"
        "mov sp, r0         \n\t"
        "msr msp, r0        \n\t"
        "mov r0, #0         \n\t"
        "msr psp, r0        \n\t"
    );
    volatile uint32_t *p = (uint32_t *)0x00000000;
    *p = 0xDEADBEEF;
    while (1);
}
