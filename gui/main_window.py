# -*- coding: utf-8 -*-
"""
主窗口：串口设置、文件选择、升级控制、日志显示
支持配置持久化（串口、波特率、重传次数、超时时间、固件路径）
"""

import os
import sys
from datetime import datetime
from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QComboBox, QLabel, QLineEdit,
                             QTextEdit, QProgressBar, QFileDialog, QMessageBox,
                             QSpinBox)
from PyQt5.QtCore import Qt, QMetaObject, Q_ARG, QSettings
from PyQt5.QtGui import QIcon
from PyQt5.QtSerialPort import QSerialPortInfo

from serial.serial_worker import SerialWorkerThread
from firmware.loader import load_firmware, FirmwareLoaderError
from PyQt5.QtCore import Qt, QMetaObject, Q_ARG, QSettings, QTimer


def resource_path(relative_path):
    """获取资源的绝对路径，兼容 PyInstaller 打包"""
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FH_STREAM 固件升级工具")
        self.setMinimumSize(800, 600)

        # 设置窗口图标
        icon_path = resource_path('icon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.serial_thread = None
        self.worker = None
        self.firmware_data = None

        # 加载保存的配置
        self.settings = QSettings("FH_STREAM", "FirmwareUpdater")
        self._load_settings()

        self._init_ui()
        self._refresh_ports()

        # 恢复固件文件（如果有保存的路径）
        if self.saved_firmware_path and os.path.exists(self.saved_firmware_path):
            self.file_edit.setText(self.saved_firmware_path)
            try:
                self.firmware_data = load_firmware(self.saved_firmware_path)
                self._log(f"自动加载上次固件: {self.saved_firmware_path} ({len(self.firmware_data)} bytes)")
                if self.serial_thread is not None:
                    self.start_btn.setEnabled(True)
            except FirmwareLoaderError as e:
                self._log(f"自动加载固件失败: {e}")
                self.firmware_data = None
        else:
            self.file_edit.clear()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # ========== 串口配置区域 ==========
        serial_layout = QHBoxLayout()
        serial_layout.setSpacing(0)
        serial_layout.addWidget(QLabel("串口:"))
        self.port_combo = QComboBox()
        serial_layout.addWidget(self.port_combo)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._refresh_ports)
        serial_layout.addWidget(self.refresh_btn)

        serial_layout.addSpacing(20)
        serial_layout.addWidget(QLabel("波特率:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "19200", "38400", "57600", "115200", "230400"])
        # 恢复保存的波特率
        index = self.baud_combo.findText(self.saved_baud)
        if index >= 0:
            self.baud_combo.setCurrentIndex(index)
        else:
            self.baud_combo.setCurrentText("115200")
        serial_layout.addWidget(self.baud_combo)

        # 让“打开串口”按钮紧跟在波特率后面
        self.open_btn = QPushButton("打开串口")
        self.open_btn.clicked.connect(self._toggle_serial)
        serial_layout.addWidget(self.open_btn)
        serial_layout.addStretch()
        layout.addLayout(serial_layout)

        # ========== 重传参数设置区域 ==========
        param_layout = QHBoxLayout()
        param_layout.addWidget(QLabel("重传次数:"))
        self.retry_spin = QSpinBox()
        self.retry_spin.setRange(1, 20)
        self.retry_spin.setValue(self.saved_retries)
        self.retry_spin.setToolTip("每帧最大重试次数（1~20）")
        param_layout.addWidget(self.retry_spin)

        param_layout.addWidget(QLabel("超时时间(ms):"))
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(100, 10000)
        self.timeout_spin.setValue(self.saved_timeout)
        self.timeout_spin.setSingleStep(100)
        self.timeout_spin.setToolTip("等待ACK的超时时间（毫秒）")
        param_layout.addWidget(self.timeout_spin)

        param_layout.addStretch()
        layout.addLayout(param_layout)

        # ========== 固件文件选择 ==========
        file_layout = QHBoxLayout()
        file_layout.addWidget(QLabel("固件文件:"))
        self.file_edit = QLineEdit()
        self.file_edit.setReadOnly(True)
        file_layout.addWidget(self.file_edit)
        self.browse_btn = QPushButton("浏览...")
        self.browse_btn.clicked.connect(self._browse_file)
        file_layout.addWidget(self.browse_btn)
        layout.addLayout(file_layout)

        # ========== 控制按钮 ==========
        ctrl_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始升级")
        self.start_btn.setEnabled(False)
        self.start_btn.clicked.connect(self._start_upgrade)
        ctrl_layout.addWidget(self.start_btn)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_upgrade)
        ctrl_layout.addWidget(self.stop_btn)
        layout.addLayout(ctrl_layout)

        # ========== 进度条 ==========
        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        # ========== 日志区域 ==========
        layout.addWidget(QLabel("日志:"))
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

    def _load_settings(self):
        """加载保存的配置"""
        self.saved_serial_port = self.settings.value("serial_port", "", type=str)
        self.saved_baud = self.settings.value("baud_rate", "115200", type=str)
        self.saved_retries = int(self.settings.value("max_retries", 5, type=int))
        self.saved_timeout = int(self.settings.value("ack_timeout_ms", 1000, type=int))
        self.saved_firmware_path = self.settings.value("firmware_path", "", type=str)

    def _save_settings(self):
        """保存当前配置"""
        self.settings.setValue("serial_port", self.port_combo.currentText())
        self.settings.setValue("baud_rate", self.baud_combo.currentText())
        self.settings.setValue("max_retries", self.retry_spin.value())
        self.settings.setValue("ack_timeout_ms", self.timeout_spin.value())
        if self.file_edit.text():
            self.settings.setValue("firmware_path", self.file_edit.text())

    def closeEvent(self, event):
        """窗口关闭时保存配置"""
        self._save_settings()
        # 关闭串口线程等
        if self.serial_thread:
            if self.worker:
                self.worker.stop_serial_signal.emit()
            self.serial_thread.quit()
            self.serial_thread.wait()
        event.accept()

    def _refresh_ports(self):
        self.port_combo.clear()
        ports = QSerialPortInfo.availablePorts()
        port_names = [p.portName() for p in ports]
        self.port_combo.addItems(port_names)
        if self.port_combo.count() == 0:
            self.port_combo.addItem("无可用串口")
        else:
            # 尝试恢复上次选择的串口
            if self.saved_serial_port and self.saved_serial_port in port_names:
                self.port_combo.setCurrentText(self.saved_serial_port)
            else:
                self.port_combo.setCurrentIndex(0)

    def _toggle_serial(self):
        if self.serial_thread is None:
            # 打开串口
            port = self.port_combo.currentText()
            if port == "无可用串口":
                QMessageBox.warning(self, "警告", "没有可用的串口")
                return
            baud = int(self.baud_combo.currentText())

            self.serial_thread = SerialWorkerThread(self)
            self.worker = self.serial_thread.worker
            self.worker.configure(port, baud)
            self.worker.log_signal.connect(self._log)
            self.worker.finished_signal.connect(self._on_upgrade_finished)
            self.worker.progress_signal.connect(self._update_progress)
            self.serial_thread.start()
            # 等待线程初始化完成再发送打开信号
            QTimer.singleShot(50, lambda: self.worker.start_serial_signal.emit())

            self.open_btn.setText("关闭串口")
            self.start_btn.setEnabled(bool(self.firmware_data))
            self._log(f"打开串口 {port}")
        else:
            # 关闭串口
            if self.worker:
                self.worker.stop_serial_signal.emit()
            # 等待串口完全关闭
            QTimer.singleShot(500, self._finalize_serial_close)
    
    def _finalize_serial_close(self):
        """延迟关闭线程，确保端口释放"""
        if self.serial_thread:
            self.serial_thread.quit()
            if not self.serial_thread.wait(2000):
                self.serial_thread.terminate()
                self.serial_thread.wait()
            self.serial_thread.deleteLater()
            self.serial_thread = None
            self.worker = None
            self.open_btn.setText("打开串口")
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self._log("串口已关闭")

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择固件", "", "固件 (*.bin *.hex *.elf);;所有文件 (*)")
        if path:
            self.file_edit.setText(path)
            try:
                self.firmware_data = load_firmware(path)
                self._log(f"加载成功: {path} ({len(self.firmware_data)} bytes)")
                if self.serial_thread is not None:
                    self.start_btn.setEnabled(True)
                # 保存路径（但不立即保存到磁盘，在关闭窗口时统一保存）
            except FirmwareLoaderError as e:
                QMessageBox.critical(self, "错误", f"加载失败: {e}")
                self.firmware_data = None
                self.start_btn.setEnabled(False)

    def _start_upgrade(self):
        if self.firmware_data is None:
            QMessageBox.warning(self, "警告", "请先选择固件文件")
            return
        if self.worker is None:
            QMessageBox.warning(self, "警告", "请先打开串口")
            return

        # 应用用户设置的重传参数
        self.worker.max_retries = self.retry_spin.value()
        self.worker.ack_timeout_ms = self.timeout_spin.value()
        self._log(f"使用参数: 重传次数={self.worker.max_retries}, 超时时间={self.worker.ack_timeout_ms}ms")

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.progress.setValue(0)

        QMetaObject.invokeMethod(self.worker, "send_firmware",
                                 Qt.QueuedConnection,
                                 Q_ARG(bytes, self.firmware_data),
                                 Q_ARG(int, 251))

    def _stop_upgrade(self):
        if self.worker:
            self.worker.request_stop()
            self._log("正在停止升级...")
            self.stop_btn.setEnabled(False)
        else:
            self._log("没有正在进行的升级")

    def _on_upgrade_finished(self, success: bool, message: str):
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        if success:
            QMessageBox.information(self, "完成", message)
        else:
            QMessageBox.critical(self, "失败", message)
        self._log(message)

    def _update_progress(self, current, total):
        self.progress.setMaximum(total)
        self.progress.setValue(current)

    def _log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")