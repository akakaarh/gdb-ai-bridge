# GDB-AI Bridge demo script
# Usage: arm-none-eabi-gdb-py -x scripts/demo.gdb firmware.elf

# Load the bridge
source gdb_bridge/gdb_bridge.py

# Configure for STM32MP157 M4 bare-metal
ai config arch arm target baremetal

# Show config
ai info

# When a crash occurs, collect context:
# ai collect
# ai dump crash.json
