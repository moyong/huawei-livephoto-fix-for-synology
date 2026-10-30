"""Google Motion Photo 的 XMP 构造与识别。

关键点：`MicroVideoOffset` 是从文件末尾往回数的字节数。在 SOI 之后插入 N 字节后，
文件总长与视频起点同时 +N，因此该偏移值保持不变 —— 已写入的值不需要、也不允许改动。
"""
import struct

XMP_NS = b"http://ns.adobe.com/xap/1.0/"

# 命中任意一个即视为"已声明为动态照片"
GCAMERA_DECL = (b"GCamera:MotionPhoto", b"GCamera:MicroVideo")

# 我们写入的 7 个 GCamera 属性，缺一不可（DSM 的 exiv2 也是按这个数目被校验的）
DECL_KEYS = (
    "MotionPhoto",
    "MotionPhotoVersion",
    "MotionPhotoPresentationTimestampUs",
    "MicroVideo",
    "MicroVideoVersion",
    "MicroVideoOffset",
    "MicroVideoPresentationTimestampUs",
)

_PACKET_BEGIN = "<?xpacket"
_PACKET_END = "<?xpacket end"

_TEMPLATE = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Adobe XMP Core 5.1.0-jc003">\n'
    ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
    '  <rdf:Description rdf:about=""\n'
    '    xmlns:GCamera="http://ns.google.com/photos/1.0/camera/"\n'
    '   GCamera:MotionPhoto="1"\n'
    '   GCamera:MotionPhotoVersion="1"\n'
    '   GCamera:MotionPhotoPresentationTimestampUs="%d"\n'
    '   GCamera:MicroVideo="1"\n'
    '   GCamera:MicroVideoVersion="1"\n'
    '   GCamera:MicroVideoOffset="%d"\n'
    '   GCamera:MicroVideoPresentationTimestampUs="%d"/>\n'
    ' </rdf:RDF>\n'
    '</x:xmpmeta>\n'
    '<?xpacket end="w"?>'
)


def has_declaration(data):
    """是否已含 Google Motion Photo 声明。"""
    return any(k in data for k in GCAMERA_DECL)


def has_xmp(data):
    """是否已含任意 XMP 包（不论是哪种）。"""
    return XMP_NS in data


def build_app1(duration_us, offset):
    """构造完整的 XMP APP1 段（含 FFE1 marker 与长度字段）。"""
    body = _TEMPLATE % (duration_us, offset, duration_us)
    payload = XMP_NS + b"\x00" + body.encode("utf-8")
    seglen = len(payload) + 2
    if seglen > 0xFFFF:
        raise ValueError("APP1 segment exceeds 64KB")
    return b"\xff\xe1" + struct.pack(">H", seglen) + payload


def insert(data, duration_us, offset):
    """把 XMP APP1 段插到 SOI 之后，返回新字节串。"""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG (missing SOI)")
    return data[:2] + build_app1(duration_us, offset) + data[2:]


def find_app1(data):
    """定位 XMP APP1 段，返回 (段起始, 段结束, 载荷) 或 None。

    只走在 SOI 之后的连续 marker 链上，遇到 SOS(0xDA) 即止 —— XMP 必须在这之前。
    """
    i, n = 2, len(data)
    while i + 4 <= n:
        if data[i] != 0xFF:
            return None
        marker = data[i + 1]
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker in (0xD9, 0xDA):
            return None
        seglen = struct.unpack(">H", data[i + 2:i + 4])[0]
        if seglen < 2 or i + 2 + seglen > n:
            return None
        payload = data[i + 4:i + 2 + seglen]
        if marker == 0xE1 and payload.startswith(XMP_NS):
            return i, i + 2 + seglen, payload[len(XMP_NS) + 1:]
        i += 2 + seglen
    return None


def parse_declared(data):
    """解析文件头部的 XMP 段，返回 {属性名: 值}（不含 GCamera: 前缀）。

    没有该段或结构不完整时返回 {}。用于不依赖 exiv2 的自校验：
    群晖的 exiv2 能读、我们自己也能读，两边对不上才说明有问题。
    """
    seg = find_app1(data)
    if not seg:
        return {}
    _, _end, body = seg
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return {}
    if _PACKET_BEGIN not in text or _PACKET_END not in text:
        return {}                      # 段被截断或不是完整 xpacket
    if not text.rstrip().endswith("?>"):
        return {}                      # 尾部不完整（padding 允许）
    out = {}
    for key in DECL_KEYS:
        tag = "GCamera:%s=" % key
        j = text.find(tag)
        if j < 0:
            continue
        q = text[j + len(tag)]
        if q not in "\"'":
            continue
        k = text.find(q, j + len(tag) + 1)
        if k < 0:
            continue
        out[key] = text[j + len(tag) + 1:k]
    return out


def count_fields(data):
    """按内置解析统计 GCamera 属性个数，无法解析返回 None。"""
    seg = find_app1(data)
    if not seg:
        return None
    return len(parse_declared(data))
