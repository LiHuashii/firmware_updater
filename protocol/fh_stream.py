# -*- coding: utf-8 -*-
"""
FH_STREAM 协议封装
帧格式: | HEAD(0x55) | TAG(1) | LEN(1) | VALUE(n) | CRC(1) |
CRC固定为0xEE（不做实际校验）
"""

class FhStreamProtocol:
    HEAD = 0x55
    CRC_FIXED = 0xEE

    TAG_DATA = 0x00
    TAG_CMD  = 0x01
    TAG_ACK  = 0x02

    @staticmethod
    def pack(tag: int, value: bytes) -> bytes:
        """
        打包一帧数据
        :param tag:   帧类型 (DATA/CMD/ACK)
        :param value: 负载数据 (bytes)
        :return:      完整帧数据
        """
        length = len(value)
        if length > 255:
            raise ValueError(f"value length {length} exceeds 255")

        frame = bytearray()
        frame.append(FhStreamProtocol.HEAD)
        frame.append(tag)
        frame.append(length)
        frame.extend(value)
        frame.append(FhStreamProtocol.CRC_FIXED)   # CRC固定为0xEE
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
            "crc": None
        }
        返回 (event, frame_dict)
        event: None / "FRAME_RECEIVED" / "ERROR_CRC"
        """
        state = state_machine["state"]

        if state == "IDLE":
            if byte == FhStreamProtocol.HEAD:
                state_machine["state"] = "TAG"
                state_machine["head"] = byte
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
            state_machine["crc"] = byte
            # CRC固定为0xEE，不实际校验，直接认为正确
            if byte != FhStreamProtocol.CRC_FIXED:
                # 虽然是固定值，但若下位机错误发送，仍然报错
                event = "ERROR_CRC"
            else:
                event = "FRAME_RECEIVED"
            # 复制帧数据
            frame_copy = {
                "head": state_machine["head"],
                "tag": state_machine["tag"],
                "length": state_machine["length"],
                "value": bytes(state_machine["value"]),
                "crc": state_machine["crc"]
            }
            # 重置状态机
            state_machine["state"] = "IDLE"
            state_machine["head"] = None
            state_machine["tag"] = None
            state_machine["length"] = None
            state_machine["value"] = bytearray()
            state_machine["crc"] = None
            return event, frame_copy

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
        value = packet_id.to_bytes(4, 'little') + data_chunk
        return FhStreamProtocol.pack(FhStreamProtocol.TAG_DATA, value)

    @staticmethod
    def extract_ack_id(ack_frame_value: bytes) -> int:
        """
        从ACK帧的VALUE中提取ID（前4字节）
        """
        if len(ack_frame_value) < 4:
            raise ValueError("ACK frame value too short")
        return int.from_bytes(ack_frame_value[:4], 'little')