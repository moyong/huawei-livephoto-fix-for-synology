"""MP4 box 解析工具。

只做三件事：遍历顶级 box、定位尾部视频起点、从 moov/mvhd 读时长。
刻意不依赖任何第三方库，保持 Python 3.8 可跑（DSM 自带版本）。
"""
import struct

# 目标品牌：华为实测出现过的三种 ftyp major brand
BRANDS = (b"ftypmp42", b"ftypisom", b"ftypiso6")


def walk_boxes(buf, start=0, end=None, strict=False):
    """遍历顶级 box，返回 [(offset, size, type)]，支持 64-bit largesize。

    华为写的 MP4 在 mdat 之后还会带一段非 box 的残留数据（实测约 39KB），
    因此默认 strict=False：遇到不合法的 box 就停下，返回已解析出的部分。
    """
    end = len(buf) if end is None else end
    pos = start
    out = []
    while pos + 8 <= end:
        size = struct.unpack(">I", buf[pos:pos + 4])[0]
        btype = buf[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > end:
                break
            size = struct.unpack(">Q", buf[pos + 8:pos + 16])[0]
            header = 16
        elif size == 0:
            size = end - pos
        if size < header or pos + size > end:
            if strict:
                return None
            break
        if not all(32 <= c < 127 for c in btype):
            if strict:
                return None
            break
        out.append((pos, size, btype))
        pos += size
    return out


def _looks_like_ftyp(data, vstart, n):
    """vstart 处是否是一个像样的 ftyp box：size 合理，且 brand 是可打印 ASCII。

    只做这两项检查，是为了过滤 JPEG 熵编码数据里的巧合字节 —— 实测 2 万张纯静态
    照片里有 42 处出现 b"ftyp"，但它们的 size 字段是乱码、brand 不可打印
    （例如 b'ftypy\\xa1\\xc5\\xbc\\xce\\xd7\\xd1\\xc3'）。真 MP4 的 brand 一定是 ASCII。
    """
    if vstart < 0 or vstart + 8 > n:
        return False
    size = struct.unpack(">I", data[vstart:vstart + 4])[0]
    if not (16 <= size <= n - vstart):
        return False
    brand = data[vstart + 4:vstart + 8]
    return all(32 <= c < 127 for c in brand)


def has_ftyp(data):
    """data 里是否存在结构像真 box 的 ftyp（不管 brand 认不认识）。

    detect 用它区分两种情况：完全没有 ftyp（纯静态照片），和有 ftyp 但品牌不在
    白名单内（可能是另一种动态照片，值得提示人工看）。
    """
    n = len(data)
    p = data.find(b"ftyp")
    while p >= 4:
        if _looks_like_ftyp(data, p - 4, n):
            return True
        p = data.find(b"ftyp", p + 1)
    return False


def find_video_start(data):
    """定位尾部 MP4 起点。

    返回 (vstart, brand_pos)：vstart 为 ftyp box 的 size 字段偏移（即视频起点），
    brand_pos 为 major brand 字符串在文件中的偏移。找不到返回 (None, None)。

    候选位置也要过 size/brand 校验，否则 JPEG 数据里恰好拼出 "ftypmp42" 时
    会把视频起点算到一个随机的字节上。
    """
    n = len(data)
    pos = -1
    for b in BRANDS:
        p = data.find(b)
        while p > 4:
            if _looks_like_ftyp(data, p - 4, n) and (pos < 0 or p < pos):
                pos = p
                break
            p = data.find(b, p + 1)
    if pos < 0:
        return None, None
    return pos - 4, pos


def duration_us(tail):
    """从 moov/mvhd 读视频时长（微秒），失败返回 None。

    'mvhd' 的 i 指向 type 字段：version=i+4，flags=i+5..i+8
      v0: creation i+8, modification i+12, timescale i+16, duration i+20
      v1: creation i+8(8B), modification i+16(8B), timescale i+24, duration i+28(8B)
    """
    top = walk_boxes(tail)
    if not top:
        return None
    moov = next((b for b in top if b[2] == b"moov"), None)
    if moov is None:
        return None
    mo, msize, _ = moov
    seg = tail[mo:mo + msize]
    i = seg.find(b"mvhd")
    if i < 4:
        return None
    ver = seg[i + 4]
    try:
        if ver == 0:
            timescale = struct.unpack(">I", seg[i + 16:i + 20])[0]
            duration = struct.unpack(">I", seg[i + 20:i + 24])[0]
        elif ver == 1:
            timescale = struct.unpack(">I", seg[i + 24:i + 28])[0]
            duration = struct.unpack(">Q", seg[i + 28:i + 36])[0]
        else:
            return None
    except struct.error:
        return None
    if not timescale:
        return None
    return int(duration * 1000000 / timescale)
