#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FH_STREAM 固件升级工具 - 入口
"""

import sys
from PyQt5.QtWidgets import QApplication
from gui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()