# -*- coding: utf-8 -*-
"""
CRC-32 校验模块
多项式: 0x04C11DB7 (标准)
算法: 标准CRC-32 (与zlib.crc32兼容)
"""

import struct

def crc32_calc(data: bytes) -> int:
    """
    计算CRC-32校验值 (与zlib.crc32结果一致)
    :param data: 需要校验的数据
    :return: CRC-32校验值（32位整数）
    """
    # 使用Python内置的zlib.crc32，这是标准CRC-32实现
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF