"""判别：一个文件是不是"缺 XMP 声明的动态照片"。

全部通过才返回 FIX，任一不过即返回 SKIP_* 并给出可读原因。
主判据是文件结构，不是拍摄品牌。

    1) 扩展名 .jpg / .jpeg
    2) 首两字节 = FFD8（JPEG SOI）
    3) 文件内存在 ftypmp42 / ftypisom / ftypiso6，且位置 > 4
    4) 该位置 -4 处是一个结构自洽的 MP4 box：size 合理、type == 'ftyp'
    5) 从该位置到文件尾可解析出 ftyp + moov + mdat，且 mvhd 时长在合理区间
另加两条否决：
    A) 已含 GCamera:MotionPhoto / MicroVideo  -> 已是动态照片
    B) 已含任意 XMP 包                        -> 不二次插入
"""
import hashlib
import os
import struct

from . import exif, mp4, xmp

# mvhd 时长闸门（微秒）：0.2s ~ 60s。超出视为不是动态照片视频。
DURATION_MIN_US = 200000
DURATION_MAX_US = 60000000

# 快判窗口：先读这么多字节，里面已有 GCamera 声明就直接判 SKIP_ALREADY，不再读全文。
# 我们自己写入的 XMP 紧跟 SOI（偏移 2），实际几 KB 就够；给到 256KB 是为了覆盖
# exiftool 之类把 XMP 放在 EXIF 之后的工具。命中不了就走完整判定，结论不变，只是慢。
HEAD_BYTES = 256 * 1024


def classify(path):
    """返回 (verdict, detail, ctx)。verdict == "FIX" 表示可修补。

    只负责"读文件"和"快判"，判定本体在 verdict_of_bytes，便于 verify 复用同一套判据。
    """
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    if ext not in (".jpg", ".jpeg"):
        return "SKIP_EXT", "not a .jpg/.jpeg", None

    with open(path, "rb") as f:
        head = f.read(HEAD_BYTES)
        if head[:2] != b"\xff\xd8":
            return "SKIP_NOT_JPEG", "no JPEG SOI", None
        # 快判：已修补过的文件在这里就结束，不读剩下那十几 MB
        if xmp.has_declaration(head):
            make, model = exif.make_model(head)
            return ("SKIP_ALREADY", "already declares a GCamera Motion Photo",
                    {"make": make, "model": model,
                     "size": os.fstat(f.fileno()).st_size})
        data = head + f.read()

    return verdict_of_bytes(data)


def verdict_of_bytes(data):
    """对完整文件内容做判定，返回 (verdict, detail, ctx)。

    与 classify 的差别：不做扩展名检查、不做头部快判，调用方负责把整个文件读完。
    verify 需要区分"待修"和"静态照片"，用的就是这个函数 —— 判据只有这一处。
    """
    n = len(data)
    if n < 2 or data[:2] != b"\xff\xd8":
        return "SKIP_NOT_JPEG", "no JPEG SOI", None
    make, model = exif.make_model(data)
    meta = {"make": make, "model": model, "size": n}

    if not mp4.has_ftyp(data):
        return "SKIP_NO_FTYP", "no ftyp anywhere - plain still image", meta
    if xmp.has_declaration(data):       # 兜底：声明落在 HEAD_BYTES 之后
        return "SKIP_ALREADY", "already declares a GCamera Motion Photo", meta
    if xmp.has_xmp(data):
        return "SKIP_HAS_XMP", "has an XMP block, but not a Motion Photo - needs manual review", meta

    vstart, brand_pos = mp4.find_video_start(data)
    if vstart is None:
        return "SKIP_BRAND", "ftyp brand is not mp42/isom/iso6", meta

    box_size = struct.unpack(">I", data[vstart:vstart + 4])[0]
    box_type = data[vstart + 4:vstart + 8]
    if box_type != b"ftyp":
        return "SKIP_BOX", "4 bytes before ftyp are not a valid box size (type=%r)" % box_type, meta
    if box_size < 16 or box_size > n - vstart:
        return "SKIP_BOX", "invalid ftyp box size=%d" % box_size, meta

    eoi = data.rfind(b"\xff\xd9", 0, vstart)
    if eoi < 0:
        return "SKIP_NO_EOI", "no JPEG EOI before the video segment", meta

    tail = data[vstart:]
    top = mp4.walk_boxes(tail)
    if not top:
        return "SKIP_MP4", "cannot parse the top-level box chain of the MP4 tail", meta
    types = [b[2] for b in top]
    if types[0] != b"ftyp" or b"moov" not in types or b"mdat" not in types:
        return ("SKIP_MP4",
                "MP4 tail is missing ftyp/moov/mdat (%s)"
                % b",".join(types[:6]).decode("latin1"), meta)

    dur = mp4.duration_us(tail)
    if not dur:
        return "SKIP_MP4", "cannot read duration from moov/mvhd", meta
    if not (DURATION_MIN_US <= dur <= DURATION_MAX_US):
        return "SKIP_MP4", "implausible duration %.3fs (not a Motion Photo video)" % (dur / 1e6), meta

    ctx = {
        "size": n,
        "make": make,
        "model": model,
        "vstart": vstart,
        "offset": n - vstart,
        "duration_us": dur,
        "gap": vstart - (eoi + 2),
        "brand": data[brand_pos:brand_pos + 8].decode("latin1"),
        "tail_sha": hashlib.sha256(tail).hexdigest(),
    }
    return "FIX", "Huawei Motion Photo without an XMP declaration", ctx
