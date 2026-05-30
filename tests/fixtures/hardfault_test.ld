MEMORY
{
    RAM (xrw) : ORIGIN = 0x10000000, LENGTH = 256K
}

_estack = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
    .isr_vector :
    {
        . = ALIGN(4);
        KEEP(*(.isr_vector))
        . = ALIGN(4);
    } > RAM

    .text :
    {
        . = ALIGN(4);
        *(.text)
        *(.text*)
        . = ALIGN(4);
    } > RAM

    .data :
    {
        . = ALIGN(4);
        *(.data)
        *(.data*)
        . = ALIGN(4);
    } > RAM
}
