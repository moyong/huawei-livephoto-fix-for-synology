"""从 JPEG 的 APP1 Exif 段读 Make / Model。

只用于可选的 `--make-allow` 品牌白名单闸门，主判据不看品牌。
"""
import struct


def make_model(data):
    """返回 (make, model)，字段缺失或格式异常时返回 (None, None)。"""
    i = 2
    n = len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None, None
        marker = data[i + 1]
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xD9, 0xDA):
            return None, None
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if seglen < 2:
            return None, None
        if marker == 0xE1 and data[i + 4:i + 10] == b"Exif\x00\x00":
            try:
                return _read_tiff(data, i + 10)
            except Exception:
                return None, None
        i += 2 + seglen
    return None, None


def _read_tiff(buf, base):
    order = buf[base:base + 2]
    if order == b"II":
        e = "<"
    elif order == b"MM":
        e = ">"
    else:
        return None, None
    if struct.unpack(e + "H", buf[base + 2:base + 4])[0] != 42:
        return None, None
    ifd = base + struct.unpack(e + "I", buf[base + 4:base + 8])[0]
    cnt = struct.unpack(e + "H", buf[ifd:ifd + 2])[0]
    out = {}
    for k in range(cnt):
        p = ifd + 2 + k * 12
        if p + 12 > len(buf):
            break
        tag = struct.unpack(e + "H", buf[p:p + 2])[0]
        if tag not in (0x010F, 0x0110):
            continue
        typ = struct.unpack(e + "H", buf[p + 2:p + 4])[0]
        num = struct.unpack(e + "I", buf[p + 4:p + 8])[0]
        if typ != 2:  # ASCII
            continue
        if num <= 4:
            raw = buf[p + 8:p + 8 + num]
        else:
            off = base + struct.unpack(e + "I", buf[p + 8:p + 12])[0]
            raw = buf[off:off + num]
        out[tag] = raw.split(b"\x00")[0].decode("utf-8", "replace").strip()
    return out.get(0x010F), out.get(0x0110)
