#!/bin/zsh
# Double-click (opens in Terminal.app) or run:  open -a Terminal run_gui.command
#
# macOS TCC: BLE from Python only works when the *responsible* app declares
# NSBluetoothAlwaysUsageDescription. Terminal.app / iTerm2 do; Warp and some
# others don't, and the interpreter is killed with "abort" (SIGABRT).
cd "$(dirname "$0")"
PY="${PYTHON:-$HOME/miniconda3/bin/python3}"
exec "$PY" dimmer_gui.py
