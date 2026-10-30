"""合成测试样本：几 KB 的假 JPEG + 假 MP4，覆盖各种判定分支。

不用真实照片的理由：真实照片动辄 10MB，且涉及隐私，绝不能进仓库。
合成样本足以覆盖结构判据 —— 判据本来就是按字节结构判断的。
"""
import os
import struct

APP0 = (b"\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
SOI = b"\xff\xd8"
EOI = b"\xff\xd9"


def jpeg(payload=b"", jpeg_body=b"\x11" * 64):
    """最小 JPEG：SOI + APP0 + [payload] + 图像数据 + EOI。"""
    return SOI + APP0 + payload + jpeg_body + EOI


def xmp_segment(body):
    """把一个 XMP 文本包成 APP1 段。"""
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + body.encode("utf-8")
    return b"\xff\xe1" + struct.pack(">H", len(payload) + 2) + payload


XMP_OTHER = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/></x:xmpmeta>'
    '<?xpacket end="w"?>'
)

XMP_DECLARED = (
    '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF '
    'xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" '
    'xmlns:GCamera="http://ns.google.com/photos/1.0/camera/" '
    'GCamera:MicroVideoOffset="1234"/></rdf:RDF></x:xmpmeta>'
    '<?xpacket end="w"?>'
)


def box(btype, payload):
    return struct.pack(">I", len(payload) + 8) + btype + payload


def ftyp(brand=b"mp42"):
    return box(b"ftyp", brand + b"\x00\x00\x00\x00" + brand)


def mvhd(duration_units=3000000, timescale=1000000):
    payload = (b"\x00\x00\x00\x00"                  # version 0 + flags
               + b"\x00\x00\x00\x00"                # creation time
               + b"\x00\x00\x00\x00"                # modification time
               + struct.pack(">I", timescale)
               + struct.pack(">I", duration_units))
    return box(b"mvhd", payload)


def moov(duration_units=3000000, timescale=1000000):
    return box(b"moov", mvhd(duration_units, timescale))


def mdat(size=16):
    return box(b"mdat", b"\x00" * size)


def mp4_tail(duration_units=3000000, timescale=1000000, brand=b"mp42"):
    """完整的 ftyp + moov + mdat 链，尾部带一段非 box 残留（模仿华为）。"""
    return (ftyp(brand) + moov(duration_units, timescale) + mdat()
            + b"\x00garbage-residue\x00")


def motion_photo(name="IMG_motion.jpg", brand=b"mp42", duration_units=3000000):
    """标准待修补样本：JPEG + 尾部 MP4，无任何 XMP。"""
    return name, jpeg() + mp4_tail(duration_units=duration_units, brand=brand)


def static_photo(name="IMG_static.jpg"):
    """纯静态照片：没有任何 ftyp。"""
    return name, jpeg(jpeg_body=b"\x22" * 128)


def declared_photo(name="IMG_declared.jpg"):
    """已经是动态照片：已含 GCamera 声明。"""
    return name, jpeg(payload=xmp_segment(XMP_DECLARED)) + mp4_tail()


def other_xmp_photo(name="IMG_otherxmp.jpg"):
    """含别的 XMP 包，但不是动态照片。"""
    return name, jpeg(payload=xmp_segment(XMP_OTHER)) + mp4_tail()


def broken_tail_photo(name="IMG_badtail.jpg"):
    """有 ftyp box，但后面没有 moov/mdat。"""
    return name, jpeg() + ftyp() + b"\x00" * 40


def short_clip_photo(name="IMG_short.jpg"):
    """视频只有 0.05 秒，低于闸门下限。"""
    return name, jpeg() + mp4_tail(duration_units=50000, timescale=1000000)


ALL_FIXTURES = [
    motion_photo,
    static_photo,
    declared_photo,
    other_xmp_photo,
    broken_tail_photo,
    short_clip_photo,
]


def write_fixtures(dirpath):
    """把所有样本写进目录，返回 {文件名: 生成函数}。"""
    os.makedirs(dirpath, exist_ok=True)
    made = {}
    for fn in ALL_FIXTURES:
        name, data = fn()
        with open(os.path.join(dirpath, name), "wb") as f:
            f.write(data)
        made[name] = fn
    return made
