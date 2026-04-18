import struct
hex_data = bytes([0x31, 0x32, 0x33, 0x34, 0x35, 0x36, 0x37, 0x38, 0x39])
def crc32_calc(data: bytes) -> int:
    """
    计算CRC-32校验值 (与zlib.crc32结果一致)
    :param data: 需要校验的数据
    :return: CRC-32校验值（32位整数）
    """
    # 使用Python内置的zlib.crc32，这是标准CRC-32实现
    import zlib
    return zlib.crc32(data) & 0xFFFFFFFF

print(hex(crc32_calc(hex_data)))