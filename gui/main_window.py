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
                             QSpinBox, QSplitter, QGroupBox, QRadioButton,
                             QButtonGroup, QCheckBox, QScrollBar)
from PyQt5.QtCore import Qt, QMetaObject, Q_ARG, QSettings, QTimer, QDateTime
from PyQt5.QtGui import QIcon, QTextCursor
from PyQt5.QtSerialPort import QSerialPortInfo

from serial.serial_worker import SerialWorkerThread
from firmware.loader import load_firmware, FirmwareLoaderError


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
        self.setWindowTitle("FH_STREAM 固件升级工具 V1.0")
        self.setMinimumSize(1000, 700)

        # 设置窗口图标
        icon_path = resource_path('icon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.serial_thread = None
        self.worker = None
        self.firmware_data = None
        self.rx_display_mode = "string"  # "string" 或 "hex"
        
        # 存储所有RX数据（用于切换模式时重新显示）
        self.rx_data_history = []  # 存储 (data, timestamp) 元组，timestamp可能为None
        
        # RX 数据缓存（用于帧合并）
        self.rx_buffer = bytearray()
        self.last_rx_time = None
        self.frame_timer = QTimer()
        self.frame_timer.setSingleShot(True)
        self.frame_timer.timeout.connect(self._flush_rx_buffer)

        # 加载保存的配置
        self.settings = QSettings("FH_STREAM", "FirmwareUpdater")
        self._load_settings()

        self._init_ui()
        self._refresh_ports()
        
        # 应用保存的显示模式设置
        self._apply_saved_display_settings()

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
        self.port_combo.setMinimumWidth(60)
        serial_layout.addWidget(self.port_combo)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self._refresh_ports)
        serial_layout.addWidget(self.refresh_btn)

        serial_layout.addSpacing(20)
        serial_layout.addWidget(QLabel("波特率:"))
        self.baud_combo = QComboBox()
        self.baud_combo.addItems(["9600", "19200", "38400", "57600", "115200", "460800", "921600", "1000000"])
        # 恢复保存的波特率
        index = self.baud_combo.findText(self.saved_baud)
        if index >= 0:
            self.baud_combo.setCurrentIndex(index)
        else:
            self.baud_combo.setCurrentText("115200")
        serial_layout.addWidget(self.baud_combo)

        # 让"打开串口"按钮紧跟在波特率后面
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

        param_layout.addSpacing(20)
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

        # ========== 日志区域（左右分割） ==========
        # 创建分割器
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：系统日志（发送信息和系统信息）
        left_group = QGroupBox("系统日志 (TX/信息)")
        left_layout = QVBoxLayout(left_group)
        
        # 左侧工具栏：清空按钮
        left_toolbar = QHBoxLayout()
        left_toolbar.addStretch()
        self.clear_log_btn = QPushButton("清空系统日志")
        self.clear_log_btn.clicked.connect(self._clear_system_log)
        left_toolbar.addWidget(self.clear_log_btn)
        left_layout.addLayout(left_toolbar)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        left_layout.addWidget(self.log_text)

        # 右侧：RX日志
        right_group = QGroupBox("RX日志")
        right_layout = QVBoxLayout(right_group)

        # RX显示模式选择和帧合并设置
        rx_display_layout = QHBoxLayout()
        rx_display_layout.addWidget(QLabel("显示模式:"))
        self.rx_string_radio = QRadioButton("字符串")
        self.rx_hex_radio = QRadioButton("16进制")
        self.rx_string_radio.setChecked(True)
        
        # 将单选按钮加入按钮组
        self.rx_display_group = QButtonGroup()
        self.rx_display_group.addButton(self.rx_string_radio, 0)
        self.rx_display_group.addButton(self.rx_hex_radio, 1)
        self.rx_string_radio.toggled.connect(self._on_rx_display_mode_changed)
        
        rx_display_layout.addWidget(self.rx_string_radio)
        rx_display_layout.addWidget(self.rx_hex_radio)
        
        # 分隔符
        rx_display_layout.addSpacing(20)
        
        # 时间戳使能
        self.rx_timestamp_enable = QCheckBox("时间戳")
        self.rx_timestamp_enable.setChecked(self.saved_rx_timestamp_enable)
        self.rx_timestamp_enable.toggled.connect(self._on_rx_timestamp_toggled)
        rx_display_layout.addWidget(self.rx_timestamp_enable)
        
        # 时间间隔阈值
        self.rx_frame_interval = QSpinBox()
        self.rx_frame_interval.setRange(1, 9999)
        self.rx_frame_interval.setValue(self.saved_rx_frame_interval)
        self.rx_frame_interval.setSuffix(" ms")
        self.rx_frame_interval.setToolTip("连续接收数据间隔大于此值时，认为是新的一帧")
        rx_display_layout.addWidget(self.rx_frame_interval)
        
        rx_display_layout.addStretch()
        
        # 清空RX日志按钮
        self.clear_rx_btn = QPushButton("清空RX日志")
        self.clear_rx_btn.clicked.connect(self._clear_rx_log)
        rx_display_layout.addWidget(self.clear_rx_btn)
        
        right_layout.addLayout(rx_display_layout)

        self.rx_text = QTextEdit()
        self.rx_text.setReadOnly(True)
        self.rx_text.setAcceptRichText(True)  # 启用富文本支持
        right_layout.addWidget(self.rx_text)

        # 添加到分割器
        splitter.addWidget(left_group)
        splitter.addWidget(right_group)
        splitter.setSizes([500, 500])  # 设置初始宽度比例

        layout.addWidget(splitter)

    def _apply_saved_display_settings(self):
        """应用保存的显示设置"""
        # 应用显示模式
        if self.saved_rx_display_mode == "hex":
            self.rx_hex_radio.setChecked(True)
            self.rx_display_mode = "hex"
        else:
            self.rx_string_radio.setChecked(True)
            self.rx_display_mode = "string"
        
        # 应用时间戳使能
        self.rx_timestamp_enable.setChecked(self.saved_rx_timestamp_enable)
        
        # 应用时间戳间隔
        self.rx_frame_interval.setValue(self.saved_rx_frame_interval)

    def _on_rx_timestamp_toggled(self, enabled):
        """时间戳使能切换"""
        if not enabled:
            # 如果不启用时间戳，立即刷新当前缓冲区
            self._flush_rx_buffer()
        else:
            # 重新启用时，重置缓冲区
            self._flush_rx_buffer()
            self.rx_buffer = bytearray()
            self.last_rx_time = None
        
        # 保存设置
        self._save_settings()
        
        # 刷新显示（时间戳状态改变，需要重新格式化显示）
        self._refresh_rx_display()

    def _flush_rx_buffer(self):
        """刷新RX缓冲区，显示累积的数据"""
        if self.rx_buffer:
            # 显示数据，使用记录的时间戳
            self._display_rx_data(bytes(self.rx_buffer), self.last_rx_time)
            # 清空缓冲区
            self.rx_buffer = bytearray()
            self.last_rx_time = None

    def _display_rx_data(self, data: bytes, timestamp: QDateTime = None):
        """显示RX数据到右侧日志区域"""
        if not data:
            return
        
        # 保存到历史记录
        self.rx_data_history.append((data, timestamp))
        
        # 根据是否启用时间戳决定前缀
        if self.rx_timestamp_enable.isChecked() and timestamp:
            ts_str = timestamp.toString("HH:mm:ss.zzz")
            # 使用HTML格式设置时间戳为红色
            prefix = f'<span style="color: red;">[{ts_str}]</span> '
            # 启用时间戳时，每条数据换行显示
            self.rx_text.append(f"{prefix}{self._format_rx_data_html(data)}")
        else:
            # 不启用时间戳时，连续显示不换行
            # 使用 insertPlainText 追加到当前行末尾
            self.rx_text.moveCursor(QTextCursor.End)
            self.rx_text.insertPlainText(self._format_rx_data(data))
        
        # 自动滚动到底部
        self.rx_text.moveCursor(QTextCursor.End)

    def _format_rx_data(self, data: bytes) -> str:
        """格式化RX数据内容（纯文本）"""
        if self.rx_display_mode == "string":
            try:
                text = data.decode('utf-8', errors='replace')
                # 替换控制字符为可见形式
                text = ''.join(c if c.isprintable() or c == '\n' or c == '\r' else f'\\x{ord(c):02x}' for c in text)
                return text
            except:
                hex_str = ' '.join(f'{b:02X}' for b in data)
                return f"[HEX] {hex_str} "
        else:
            # 16进制模式：每个字节后都加一个空格（包括最后一个）
            hex_str = ' '.join(f'{b:02X}' for b in data)
            return hex_str + ' '  # 在末尾添加一个空格

    def _format_rx_data_html(self, data: bytes) -> str:
        """格式化RX数据内容为HTML格式（用于带时间戳的显示）"""
        if self.rx_display_mode == "string":
            try:
                text = data.decode('utf-8', errors='replace')
                # 替换控制字符为可见形式
                text = ''.join(c if c.isprintable() or c == '\n' or c == '\r' else f'\\x{ord(c):02x}' for c in text)
                # 转义HTML特殊字符
                text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                return text
            except:
                hex_str = ' '.join(f'{b:02X}' for b in data)
                return f"[HEX] {hex_str} "
        else:
            # 16进制模式：每个字节后都加一个空格（包括最后一个）
            hex_str = ' '.join(f'{b:02X}' for b in data)
            return hex_str + ' '  # 在末尾添加一个空格

    def _refresh_rx_display(self):
        """刷新RX显示（切换模式时调用）"""
        # 保存当前滚动位置
        scroll_bar = self.rx_text.verticalScrollBar()
        was_at_bottom = scroll_bar.value() >= scroll_bar.maximum() - 10
        
        # 清空显示
        self.rx_text.clear()
        
        # 重新显示所有历史数据
        for data, timestamp in self.rx_data_history:
            if self.rx_timestamp_enable.isChecked() and timestamp:
                ts_str = timestamp.toString("HH:mm:ss.zzz")
                prefix = f'<span style="color: red;">[{ts_str}]</span> '
                self.rx_text.append(f"{prefix}{self._format_rx_data_html(data)}")
            else:
                # 不启用时间戳时，连续显示不换行
                self.rx_text.moveCursor(QTextCursor.End)
                self.rx_text.insertPlainText(self._format_rx_data(data))
        
        # 如果之前在最底部，则滚动到底部
        if was_at_bottom:
            self.rx_text.moveCursor(QTextCursor.End)

    def _on_rx_data(self, data: bytes):
        """接收RX数据（右侧）"""
        # hex_debug = ' '.join(f'{b:02X}' for b in data)
        # self._log(f"RX原始数据: {hex_debug}")
        current_time = QDateTime.currentDateTime()
        
        if not self.rx_timestamp_enable.isChecked():
            # 不启用时间戳，直接显示每条数据（不显示时间戳）
            self._display_rx_data(data, None)
            return
        
        # 启用时间戳，进行帧合并
        if self.last_rx_time is None:
            # 第一帧数据
            self.rx_buffer = bytearray(data)
            self.last_rx_time = current_time
            # 启动定时器，等待可能的数据继续到达
            self.frame_timer.start(self.rx_frame_interval.value())
        else:
            # 计算时间差（毫秒）
            time_diff = self.last_rx_time.msecsTo(current_time)
            
            if time_diff < self.rx_frame_interval.value():
                # 时间间隔小于阈值，合并到当前帧
                self.rx_buffer.extend(data)
                self.last_rx_time = current_time
                # 重置定时器
                self.frame_timer.start(self.rx_frame_interval.value())
            else:
                # 时间间隔超过阈值，刷新上一帧，开始新帧
                self._flush_rx_buffer()
                self.rx_buffer = bytearray(data)
                self.last_rx_time = current_time
                self.frame_timer.start(self.rx_frame_interval.value())

    def _clear_system_log(self):
        """清空系统日志"""
        self.log_text.clear()

    def _on_rx_display_mode_changed(self):
        """RX显示模式切换"""
        if self.rx_string_radio.isChecked():
            self.rx_display_mode = "string"
        else:
            self.rx_display_mode = "hex"
        
        # 保存设置
        self._save_settings()
        
        # 重新显示所有已保存的数据
        self._refresh_rx_display()

    def _clear_rx_log(self):
        """清空RX日志"""
        self.rx_text.clear()
        # 清空历史记录
        self.rx_data_history.clear()
        # 同时清空缓冲区
        self.frame_timer.stop()
        self.rx_buffer = bytearray()
        self.last_rx_time = None

    def _load_settings(self):
        """加载保存的配置"""
        self.saved_serial_port = self.settings.value("serial_port", "", type=str)
        self.saved_baud = self.settings.value("baud_rate", "115200", type=str)
        self.saved_retries = int(self.settings.value("max_retries", 5, type=int))
        self.saved_timeout = int(self.settings.value("ack_timeout_ms", 1000, type=int))
        self.saved_firmware_path = self.settings.value("firmware_path", "", type=str)
        # RX 日志设置
        self.saved_rx_timestamp_enable = self.settings.value("rx_timestamp_enable", False, type=bool)
        self.saved_rx_frame_interval = int(self.settings.value("rx_frame_interval", 100, type=int))
        # 显示模式设置
        self.saved_rx_display_mode = self.settings.value("rx_display_mode", "string", type=str)

    def _save_settings(self):
        """保存当前配置"""
        self.settings.setValue("serial_port", self.port_combo.currentText())
        self.settings.setValue("baud_rate", self.baud_combo.currentText())
        self.settings.setValue("max_retries", self.retry_spin.value())
        self.settings.setValue("ack_timeout_ms", self.timeout_spin.value())
        if self.file_edit.text():
            self.settings.setValue("firmware_path", self.file_edit.text())
        # 保存 RX 日志设置
        self.settings.setValue("rx_timestamp_enable", self.rx_timestamp_enable.isChecked())
        self.settings.setValue("rx_frame_interval", self.rx_frame_interval.value())
        # 保存显示模式
        self.settings.setValue("rx_display_mode", self.rx_display_mode)

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
            self.worker.rx_data_signal.connect(self._on_rx_data)  # 连接RX数据信号
            self.worker.finished_signal.connect(self._on_upgrade_finished)
            self.worker.progress_signal.connect(self._update_progress)
            # 连接串口打开结果信号
            self.worker.serial_open_result_signal.connect(self._on_serial_open_result)
            self.serial_thread.start()
            # 等待线程初始化完成再发送打开信号
            QTimer.singleShot(50, lambda: self.worker.start_serial_signal.emit())

            # 暂时禁用按钮，防止重复点击
            self.open_btn.setEnabled(False)
            self.open_btn.setText("正在打开...")
            self._log(f"正在打开串口 {port}...")
        else:
            # 关闭串口
            if self.worker:
                self.worker.stop_serial_signal.emit()
            # 等待串口完全关闭
            QTimer.singleShot(500, self._finalize_serial_close)

    def _on_serial_open_result(self, success: bool, message: str):
        """串口打开结果回调"""
        self.open_btn.setEnabled(True)
        if success:
            self.open_btn.setText("关闭串口")
            self.start_btn.setEnabled(bool(self.firmware_data))
            self._log(f"串口已打开: {message}")
        else:
            # 打开失败，清理线程
            self.open_btn.setText("打开串口")
            self.start_btn.setEnabled(False)
            self._log(f"串口打开失败: {message}")
            # 清理失败的串口线程
            if self.serial_thread:
                self.serial_thread.quit()
                if not self.serial_thread.wait(2000):
                    self.serial_thread.terminate()
                    self.serial_thread.wait()
                self.serial_thread.deleteLater()
                self.serial_thread = None
                self.worker = None

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
        # 修改文件过滤器，只显示.bin文件
        path, _ = QFileDialog.getOpenFileName(self, "选择固件", "", "固件 (*.bin);;所有文件 (*)")
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
        """系统日志（左侧）"""
        ts = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{ts}] {msg}")
        # 自动滚动到底部
        self.log_text.moveCursor(QTextCursor.End)