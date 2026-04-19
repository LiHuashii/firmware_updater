# -*- coding: utf-8 -*-
"""
FH_STREAM 协议封装
帧格式: | HEAD(0x55) | TAG(1) | LEN(1) | VALUE(n) | CRC(4) |
CRC-32校验范围: TAG + LEN + VALUE
"""

import struct
import zlib


class FhStreamProtocol:
    HEAD = 0x55

    TAG_DATA = 0x00
    TAG_CMD  = 0x01
    TAG_ACK  = 0x02

    # CRC 长度（字节）
    CRC_LEN = 4

    @staticmethod
    def crc32_calc(data: bytes) -> int:
        """计算CRC-32 (标准实现，与zlib.crc32一致)"""
        return zlib.crc32(data) & 0xFFFFFFFF

    @staticmethod
    def pack(tag: int, value: bytes) -> bytes:
        """
        打包一帧数据
        帧格式: | HEAD(0x55) | TAG(1) | LEN(1) | VALUE(n) | CRC(4) |
        CRC使用小端序发送
        """
        length = len(value)
        if length > 255:
            raise ValueError(f"value length {length} exceeds 255")

        # 计算CRC: 校验范围 = TAG + LEN + VALUE
        crc_data = bytes([tag, length]) + value
        crc = FhStreamProtocol.crc32_calc(crc_data)

        frame = bytearray()
        frame.append(FhStreamProtocol.HEAD)
        frame.append(tag)
        frame.append(length)
        frame.extend(value)
        # 小端序：先发送低字节
        frame.extend(struct.pack('<I', crc))
        return bytes(frame)

    @staticmethod
    def unpack_byte(byte: int, state_machine: dict) -> tuple:
        """
        状态机解包，每次喂入一个字节
        state_machine = {
            "state": "IDLE",   # IDLE, TAG, LEN, VALUE, CRC
            "head": None,
            "tag": None,
            "length": None,
            "value": bytearray(),
            "crc": bytearray()  # 收集CRC字节
        }
        返回 (event, frame_dict)
        event: None / "FRAME_RECEIVED" / "ERROR_CRC"
        """
        state = state_machine["state"]

        if state == "IDLE":
            if byte == FhStreamProtocol.HEAD:
                state_machine["state"] = "TAG"
                state_machine["head"] = byte
                # 重置其他字段
                state_machine["tag"] = None
                state_machine["length"] = None
                state_machine["value"] = bytearray()
                state_machine["crc"] = bytearray()
            return None, None

        elif state == "TAG":
            state_machine["tag"] = byte
            state_machine["state"] = "LEN"
            return None, None

        elif state == "LEN":
            state_machine["length"] = byte
            state_machine["state"] = "VALUE"
            state_machine["value"] = bytearray()
            return None, None

        elif state == "VALUE":
            state_machine["value"].append(byte)
            if len(state_machine["value"]) >= state_machine["length"]:
                state_machine["state"] = "CRC"
            return None, None

        elif state == "CRC":
            state_machine["crc"].append(byte)
            if len(state_machine["crc"]) >= FhStreamProtocol.CRC_LEN:
                # CRC收集完成，进行校验
                tag = state_machine["tag"]
                length = state_machine["length"]
                value = bytes(state_machine["value"])
                received_crc_bytes = bytes(state_machine["crc"])
                # 修改：使用小端序解析 '<I'
                received_crc = struct.unpack('<I', received_crc_bytes)[0]

                # 计算期望的CRC（校验范围：TAG + LEN + VALUE）
                crc_data = bytes([tag, length]) + value
                expected_crc = FhStreamProtocol.crc32_calc(crc_data)

                if received_crc != expected_crc:
                    event = "ERROR_CRC"
                else:
                    event = "FRAME_RECEIVED"

                # 复制帧数据
                frame_copy = {
                    "head": state_machine["head"],
                    "tag": tag,
                    "length": length,
                    "value": value,
                    "crc": received_crc
                }

                # 重置状态机
                state_machine["state"] = "IDLE"
                state_machine["head"] = None
                state_machine["tag"] = None
                state_machine["length"] = None
                state_machine["value"] = bytearray()
                state_machine["crc"] = bytearray()

                return event, frame_copy
            return None, None

        else:
            # 未知状态，重置
            state_machine["state"] = "IDLE"
            return None, None

    @staticmethod
    def create_data_frame(packet_id: int, data_chunk: bytes) -> bytes:
        """
        创建数据帧，VALUE = [4字节ID(little-endian)] + 固件数据
        :param packet_id:   帧序号 (0~0xFFFFFFFF)
        :param data_chunk:  固件数据块 (≤ 251字节，因为4字节ID占用了value长度)
        :return:            打包好的帧数据
        """
        if len(data_chunk) > 251:
            raise ValueError(f"data_chunk too large: {len(data_chunk)} > 251")
        # packet_id使用小端序（与原来保持一致）
        value = packet_id.to_bytes(4, 'little') + data_chunk
        return FhStreamProtocol.pack(FhStreamProtocol.TAG_DATA, value)

    @staticmethod
    def extract_ack_id(ack_frame_value: bytes) -> int:
        """
        从ACK帧的VALUE中提取ID（前4字节，小端序）
        """
        if len(ack_frame_value) < 4:
            raise ValueError("ACK frame value too short")
        return int.from_bytes(ack_frame_value[:4], 'little')