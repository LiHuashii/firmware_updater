# -*- coding: utf-8 -*-
import time
from PyQt5.QtCore import QObject, QThread, pyqtSignal, pyqtSlot, QMutex, QEventLoop, QTimer
from PyQt5.QtSerialPort import QSerialPort
from protocol.fh_stream import FhStreamProtocol


class SerialWorker(QObject):
    log_signal = pyqtSignal(str)
    rx_data_signal = pyqtSignal(bytes)  # 新增：原始RX数据信号
    progress_signal = pyqtSignal(int, int)
    finished_signal = pyqtSignal(bool, str)
    firmware_send_finished = pyqtSignal(bool)
    start_serial_signal = pyqtSignal()
    stop_serial_signal = pyqtSignal()
    serial_open_result_signal = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.serial = None
        self.port_name = ""
        self.baud_rate = 115200
        self.running = False

        self.stop_requested = False
        self.stop_mutex = QMutex()

        self.current_loop = None
        self.loop_mutex = QMutex()

        self.ack_mutex = QMutex()
        self.ack_received = False
        self.received_ack_id = None
        self.received_ack_value = None  # 新增：存储ACK的VALUE

        self.rx_state_machine = {
            "state": "IDLE",
            "head": None,
            "tag": None,
            "length": None,
            "value": bytearray(),
            "crc": bytearray()
        }

        self.max_retries = 5
        self.ack_timeout_ms = 1000

        self.start_serial_signal.connect(self.start)
        self.stop_serial_signal.connect(self.stop)

    def configure(self, port: str, baud: int):
        self.port_name = port
        self.baud_rate = baud

    @pyqtSlot()
    def start(self):
        """打开串口，带重试机制"""
        # 清理旧对象
        if self.serial:
            self._cleanup_serial()

        # 尝试打开，最多重试 3 次
        for attempt in range(1, 4):
            self.serial = QSerialPort()
            self.serial.setPortName(self.port_name)
            self.serial.setBaudRate(self.baud_rate)
            self.serial.setDataBits(QSerialPort.Data8)
            self.serial.setParity(QSerialPort.NoParity)
            self.serial.setStopBits(QSerialPort.OneStop)
            self.serial.setFlowControl(QSerialPort.NoFlowControl)

            if self.serial.open(QSerialPort.ReadWrite):
                self.serial.readyRead.connect(self._on_ready_read)
                self.running = True
                self.serial_open_result_signal.emit(True, f"{self.port_name} @ {self.baud_rate}")
                self.log_signal.emit(f"串口已打开: {self.port_name} @ {self.baud_rate}")
                return
            else:
                error = self.serial.errorString()
                self.log_signal.emit(f"打开尝试 {attempt}/3 失败: {error}")
                self.serial.deleteLater()
                self.serial = None
                if attempt < 3:
                    time.sleep(0.5)

        # 所有尝试都失败
        self.serial_open_result_signal.emit(False, f"{self.port_name}")
        self.finished_signal.emit(False, f"串口打开失败: {self.port_name}")
        self.log_signal.emit(f"最终打开失败: {self.port_name}")

    def _cleanup_serial(self):
        """彻底清理串口对象"""
        if self.serial:
            try:
                self.serial.disconnect()
                if self.serial.isOpen():
                    if self.serial.bytesToWrite() > 0:
                        self.serial.waitForBytesWritten(300)
                    self.serial.clear()
                    self.serial.close()
            except:
                pass
            self.serial.deleteLater()
            self.serial = None
        time.sleep(0.1)  # 给系统时间释放端口

    @pyqtSlot()
    def stop(self):
        self.running = False
        self.request_stop()
        self._cleanup_serial()
        self.log_signal.emit("串口已关闭")

    @pyqtSlot()
    def request_stop(self):
        self.stop_mutex.lock()
        self.stop_requested = True
        self.stop_mutex.unlock()
        self.loop_mutex.lock()
        if self.current_loop:
            self.current_loop.quit()
        self.loop_mutex.unlock()

    def _is_stop_requested(self) -> bool:
        self.stop_mutex.lock()
        val = self.stop_requested
        self.stop_mutex.unlock()
        return val

    def _clear_stop_request(self):
        self.stop_mutex.lock()
        self.stop_requested = False
        self.stop_mutex.unlock()

    def _on_ready_read(self):
        if not self.running:
            return
        data = self.serial.readAll()
        # 将QByteArray转换为bytes
        raw_data = bytes(data)
        
        # 发送原始数据到RX日志（右侧）
        if raw_data:
            self.rx_data_signal.emit(raw_data)
        
        # 解析协议帧
        for i in range(len(raw_data)):
            byte_val = raw_data[i]
            byte_int = byte_val[0] if isinstance(byte_val, bytes) else byte_val
            event, frame = FhStreamProtocol.unpack_byte(byte_int, self.rx_state_machine)
            if event == "FRAME_RECEIVED":
                tag = frame["tag"]
                value = frame["value"]
                if tag == FhStreamProtocol.TAG_ACK:
                    try:
                        ack_id = int.from_bytes(value[:4], 'little')
                        ack_value = int.from_bytes(value[:4], 'little')  # ACK的VALUE就是前4字节
                        print(f"[RX] 收到ACK帧: tag=0x{tag:02X}, len={frame['length']}, value={value.hex().upper()}, ack_id={ack_id} (0x{ack_id:08X}), ack_value={ack_value}")
                        
                        self.ack_mutex.lock()
                        self.ack_received = True
                        self.received_ack_id = ack_id
                        self.received_ack_value = ack_value  # 存储ACK的VALUE
                        self.ack_mutex.unlock()
                        # self.log_signal.emit(f"收到ACK, ID={ack_id}, VALUE=0x{ack_value:08X}")
                    except Exception as e:
                        print(f"[RX] ACK解析失败: {e}")
                        self.log_signal.emit(f"ACK解析失败: {e}")
                else:
                    print(f"[RX] 收到非ACK帧: tag=0x{tag:02X}, len={frame['length']}, value={value.hex().upper()}")
                    self.log_signal.emit(f"收到非ACK帧, tag=0x{tag:02X}")
            elif event == "ERROR_CRC":
                print(f"[RX] CRC校验错误")
                self.log_signal.emit("CRC校验错误(接收帧)")

    def _clear_read_buffer(self):
        if self.serial and self.serial.isOpen():
            discarded = self.serial.bytesAvailable()
            self.serial.readAll()
            if discarded > 0:
                self.log_signal.emit(f"清空了 {discarded} 字节残留数据")

    def send_frame_and_wait_ack(self, frame: bytes, expected_id: int, timeout_ms: int = None, return_value: bool = False):
        """
        发送帧并等待ACK
        :param frame: 要发送的帧
        :param expected_id: 期望的ACK ID，-1表示不校验ID
        :param timeout_ms: 超时时间(ms)
        :param return_value: 是否返回ACK的VALUE值
        :return: 如果return_value=False，返回bool；如果return_value=True，返回(success, ack_value)
        """
        if timeout_ms is None:
            timeout_ms = self.ack_timeout_ms

        if not self.serial or not self.serial.isOpen():
            self.log_signal.emit("串口未打开，无法发送")
            return (False, None) if return_value else False

        if self._is_stop_requested():
            return (False, None) if return_value else False

        self._clear_read_buffer()
        self.rx_state_machine = {
            "state": "IDLE",
            "head": None,
            "tag": None,
            "length": None,
            "value": bytearray(),
            "crc": bytearray()
        }

        self.ack_mutex.lock()
        self.ack_received = False
        self.received_ack_id = None
        self.received_ack_value = None
        self.ack_mutex.unlock()

        self.serial.write(frame)
        self.serial.flush()
        self.log_signal.emit(f"发送帧, tag={frame[1]}, len={len(frame)}")

        loop = QEventLoop()
        self.loop_mutex.lock()
        self.current_loop = loop
        self.loop_mutex.unlock()

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)

        check_timer = QTimer()
        check_timer.timeout.connect(lambda: loop.quit() if (self.ack_received or self._is_stop_requested()) else None)
        check_timer.start(10)

        loop.exec_()

        check_timer.stop()
        timer.stop()
        self.loop_mutex.lock()
        self.current_loop = None
        self.loop_mutex.unlock()

        if self._is_stop_requested():
            self.log_signal.emit("等待ACK因停止请求而中断")
            return (False, None) if return_value else False

        self.ack_mutex.lock()
        received = self.ack_received
        ack_id = self.received_ack_id
        ack_value = self.received_ack_value
        self.ack_mutex.unlock()

        if received:
            if return_value:
                print(f"[ACK] 收到ACK, ID={ack_id}, VALUE={ack_value} (0x{ack_value:08X})")
            
            if expected_id == -1:
                # 不校验ID
                if return_value:
                    self.log_signal.emit(f"成功收到ACK (不校验ID), STATE=0x{ack_value:08X}")
                    return True, ack_value
                else:
                    self.log_signal.emit(f"成功收到ACK (不校验ID)")
                    return True
            elif ack_id == expected_id:
                if return_value:
                    self.log_signal.emit(f"成功收到匹配ACK (ID={expected_id}), VALUE=0x{ack_value:08X}")
                    return True, ack_value
                else:
                    self.log_signal.emit(f"成功收到匹配ACK (ID={expected_id})")
                    return True
            else:
                if return_value:
                    self.log_signal.emit(f"ID不匹配: 期望{expected_id}, 收到{ack_id}")
                    return False, ack_value
                else:
                    self.log_signal.emit(f"ID不匹配: 期望{expected_id}, 收到{ack_id}")
                    return False
        else:
            self.log_signal.emit(f"等待ACK超时 (ID={expected_id})")
            return (False, None) if return_value else False

    @pyqtSlot(bytes, int)
    def send_firmware(self, firmware_data: bytes, chunk_data_size: int = 251):
        self._clear_stop_request()

        if not self.serial or not self.serial.isOpen():
            self.log_signal.emit("串口未打开，无法发送固件")
            self.finished_signal.emit(False, "串口未打开")
            self.firmware_send_finished.emit(False)
            return

        if chunk_data_size > 251:
            chunk_data_size = 251

        total_size = len(firmware_data)
        num_chunks = (total_size + chunk_data_size - 1) // chunk_data_size
        self.log_signal.emit(f"固件大小: {total_size} bytes, 分为 {num_chunks} 帧")
        self.log_signal.emit(f"重传配置: 超时={self.ack_timeout_ms}ms, 最大重试={self.max_retries}")

        # 发送所有 DATA 帧
        for i in range(num_chunks):
            if self._is_stop_requested():
                self.log_signal.emit("用户停止了升级")
                self.finished_signal.emit(False, "升级已停止")
                self.firmware_send_finished.emit(False)
                return

            packet_id = i
            offset = i * chunk_data_size
            data_chunk = firmware_data[offset:offset+chunk_data_size]
            frame = FhStreamProtocol.create_data_frame(packet_id, data_chunk)

            success = False
            for retry in range(self.max_retries):
                if self._is_stop_requested():
                    self.log_signal.emit("用户停止了升级")
                    self.finished_signal.emit(False, "升级已停止")
                    self.firmware_send_finished.emit(False)
                    return

                self.log_signal.emit(f"发送第 {i+1}/{num_chunks} 帧 (ID={packet_id}) 尝试 {retry+1}/{self.max_retries}")
                if self.send_frame_and_wait_ack(frame, packet_id):
                    success = True
                    break
                if retry < self.max_retries - 1:
                    time.sleep(0.001)

            if not success:
                if self._is_stop_requested():
                    self.finished_signal.emit(False, "升级已停止")
                else:
                    self.finished_signal.emit(False, f"发送第 {i+1} 帧失败，ID={packet_id}")
                self.firmware_send_finished.emit(False)
                return

            self.progress_signal.emit(i+1, num_chunks)

        # 所有 DATA 帧发送完成，发送结束命令 CMD，VALUE为整个固件的CRC-32
        self.log_signal.emit("所有数据帧发送完成，发送结束命令（固件CRC-32）...")
        
        # 计算整个固件文件的CRC-32
        crc_data = firmware_data
        firmware_crc = FhStreamProtocol.crc32_calc(crc_data)
        
        # VALUE 为 4 字节的 CRC-32 值（小端序）
        cmd_value = firmware_crc.to_bytes(4, 'little')
        cmd_frame = FhStreamProtocol.pack(FhStreamProtocol.TAG_CMD, cmd_value)
        
        self.log_signal.emit(f"固件CRC-32: 0x{firmware_crc:08X}")

        # 等待 CMD 的 ACK，并获取 ACK 的 VALUE
        cmd_ack_ok = False
        cmd_ack_value = None
        for retry in range(self.max_retries):
            if self._is_stop_requested():
                self.log_signal.emit("用户停止了升级")
                self.finished_signal.emit(False, "升级已停止")
                self.firmware_send_finished.emit(False)
                return

            self.log_signal.emit(f"发送结束命令 (CRC=0x{firmware_crc:08X}) 尝试 {retry+1}/{self.max_retries}")
            
            # 发送并等待ACK，同时获取ACK的VALUE
            cmd_ack_ok, cmd_ack_value = self.send_frame_and_wait_ack(cmd_frame, expected_id=-1, return_value=True)
            
            if cmd_ack_ok:
                # 检查ACK的VALUE值
                if cmd_ack_value == 0:
                    self.log_signal.emit(f"结束命令收到ACK，STATE=0x{cmd_ack_value:08X}，固件校验成功！")
                    break
                else:
                    self.log_signal.emit(f"结束命令收到ACK，但STATE=0x{cmd_ack_value:08X} 固件CRC校验失败！")
                    cmd_ack_ok = False
                    break
            if retry < self.max_retries - 1:
                time.sleep(0.001)

        if not cmd_ack_ok:
            if cmd_ack_value is not None and cmd_ack_value != 0:
                self.finished_signal.emit(False, f"固件CRC校验失败，下位机返回错误码: 0x{cmd_ack_value:08X}")
            else:
                self.finished_signal.emit(False, "结束命令未收到 ACK")
            self.firmware_send_finished.emit(False)
            return

        # 成功完成
        self.finished_signal.emit(True, f"固件升级成功！共 {num_chunks} 帧，固件CRC=0x{firmware_crc:08X}")
        self.firmware_send_finished.emit(True)


class SerialWorkerThread(QThread):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.worker = SerialWorker()
        self.worker.moveToThread(self)

    def run(self):
        self.exec_()