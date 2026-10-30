"""校验：回答"这些文件现在各是什么状态"。

五种状态
  OK       已修补且可用（本地模式验结构，服务端模式还比对 @eaDir 提取的视频）
  STATIC   静态照片或非动态照片，本来就不需要处理
  MISSING  华为型动态照片但缺 XMP 声明 —— 待修（判据复用 detect.verdict_of_bytes）
  PENDING  已有声明，但群晖还没提取视频（或提取物读不到）—— 等索引即可，不是错误
  BAD      有声明但结构坏了，或与服务端提取的视频不符 —— 需要回滚

两种模式
  check_file   服务端校验（需在群晖上跑）：与 @eaDir 里已提取的视频逐字节比对，
               有备份时再比对权限位/属主/mtime/体积增量。
  check_local  本地校验（任何机器都能跑）：只依赖文件自身 —— 声明可解析、
               声明偏移 == 实际尾长、尾部 MP4 结构完整、时长在合理区间。

check_file 逐文件检查
  1. 含 GCamera 声明的文件：MicroVideoOffset 恰好 1 次
  2. 尾部 MP4 长度 + SHA-256 与服务端提取的 SYNOPHOTO_FILM_MOTION_PHOTO.mp4 完全一致
  3. 有备份时：权限位与属主和备份一样（**比值，不拿绝对值当标准** —— 群晖照片目录
     既有 777 + PhotoStation ACE 的，也有普通 644 的，两种都正常）
  4. 有备份时：mtime 与备份一致、体积增量在合理区间

关于权限：@eaDir 里的提取物是 root 所有（660 root:SynologyPhotos），普通账号读不到。
读不到不等于文件坏了 —— 判 PENDING 并写明原因，不判 BAD。
"""
import hashlib
import os
import stat

from . import detect, mp4, walk, xmp

# 体积增量允许区间（实测约 +636 B，随 MicroVideoOffset 位数浮动）
DELTA_MIN, DELTA_MAX = 600, 700

STATUS_OK = "OK"
STATUS_STATIC = "STATIC"
STATUS_MISSING = "MISSING"
STATUS_PENDING = "PENDING"
STATUS_BAD = "BAD"

# 有 ftyp 但结构说不通的几种，说明"像动态照片却坏了"，归 BAD
# 其余 SKIP_* 都表示"不是需要处理的动态照片"，归 STATIC
_UNFIXED_TO_BAD = ("SKIP_BRAND", "SKIP_BOX", "SKIP_NO_EOI", "SKIP_MP4",
                   "SKIP_NOT_JPEG")


def _undeclared_status(data):
    """没有 XMP 声明时，区分"待修"、"本来就不需要修"、"结构坏了"。"""
    verdict, detail, _ = detect.verdict_of_bytes(data)
    if verdict == "FIX":
        return STATUS_MISSING, None
    if verdict in _UNFIXED_TO_BAD:
        return STATUS_BAD, detail
    return STATUS_STATIC, None



def _perm_mismatch(path, bak):
    """权限位/属主是否与备份不同。相同返回 None。

    比的是"修完和修前一样"，不是"必须等于某个值"。早先这里硬编码 777，
    那是把 MobileBackup 那批目录（手机共享上传，恰好 777 + PhotoStation ACE）
    的现象当成了规则 —— 另一个共享目录里 644 的文件全被判成 BAD，纯误报。
    原地写（r+b + truncate）根本不碰 inode/权限/属主，能改变的只有误操作。
    """
    if os.name != "posix":
        return None
    a, b = os.stat(path), os.stat(bak)
    if stat.S_IMODE(a.st_mode) != stat.S_IMODE(b.st_mode):
        return "permission bits changed: %o -> %o" % (
            stat.S_IMODE(b.st_mode), stat.S_IMODE(a.st_mode))
    if (a.st_uid, a.st_gid) != (b.st_uid, b.st_gid):
        return "owner changed: %d:%d -> %d:%d" % (
            b.st_uid, b.st_gid, a.st_uid, a.st_gid)
    return None


def check_local(path, bak=None):
    """本地校验：只看文件自身，不需要群晖、不需要 @eaDir。

    这是"本地修完、准备上传"时的确认手段。返回 (status, info)。
    """
    with open(path, "rb") as f:
        a = f.read()

    if b"GCamera:MicroVideoOffset" not in a:
        return _undeclared_status(a)
    if a.count(b"GCamera:MicroVideoOffset") != 1:
        return STATUS_BAD, "duplicate XMP declaration"

    decl = xmp.parse_declared(a)
    if not decl:
        return STATUS_BAD, "XMP segment cannot be parsed (truncated or corrupt)"
    missing = [k for k in xmp.DECL_KEYS if k not in decl]
    if missing:
        return STATUS_BAD, "XMP is missing fields: %s" % ",".join(missing)

    try:
        declared = int(decl["MicroVideoOffset"])
    except ValueError:
        return STATUS_BAD, "MicroVideoOffset is not a number: %r" % decl["MicroVideoOffset"]

    vstart, _ = mp4.find_video_start(a)
    if vstart is None:
        return STATUS_BAD, "target brand not found"
    tail = a[vstart:]

    # 这一条是核心：声明的偏移和真实尾长不一致，群晖截出来的就是废数据
    if declared != len(tail):
        return STATUS_BAD, "declared offset %d != actual tail length %d" % (declared, len(tail))

    top = mp4.walk_boxes(tail)
    types = [b[2] for b in top]
    if not top or types[0] != b"ftyp" or b"moov" not in types or b"mdat" not in types:
        return STATUS_BAD, "MP4 tail structure incomplete (%s)" % (
            b",".join(types[:6]).decode("latin1") or "empty")
    dur = mp4.duration_us(tail)
    if not dur:
        return STATUS_BAD, "cannot read video duration"
    if not (detect.DURATION_MIN_US <= dur <= detect.DURATION_MAX_US):
        return STATUS_BAD, "implausible duration %.3fs" % (dur / 1e6)

    if bak and os.path.exists(bak):
        with open(bak, "rb") as f:
            b = f.read()
        bv, _ = mp4.find_video_start(b)
        if bv is None or hashlib.sha256(b[bv:]).digest() != hashlib.sha256(tail).digest():
            return STATUS_BAD, "MP4 tail differs from the backup"
        delta = len(a) - len(b)
        if not (DELTA_MIN <= delta <= DELTA_MAX):
            return STATUS_BAD, "size delta = %d" % delta
        if abs(os.stat(bak).st_mtime - os.stat(path).st_mtime) > 1:
            return STATUS_BAD, "mtime changed"
        why = _perm_mismatch(path, bak)
        if why:
            return STATUS_BAD, why

    return STATUS_OK, _brand(a)


def check_file(path, bak=None):
    """返回 (status, info)。status 为 OK / STATIC / MISSING / PENDING / BAD。"""
    with open(path, "rb") as f:
        a = f.read()

    if b"GCamera:MicroVideoOffset" not in a:
        return _undeclared_status(a)
    if a.count(b"GCamera:MicroVideoOffset") != 1:
        return STATUS_BAD, "duplicate XMP declaration"

    vstart, _ = mp4.find_video_start(a)
    if vstart is None:
        return STATUS_BAD, "target brand not found"
    tail = a[vstart:]

    vp = walk.server_video_path(path)
    if not os.path.exists(vp):
        return STATUS_PENDING, "Synology has not extracted the video yet"
    try:
        with open(vp, "rb") as f:
            v = f.read()
    except OSError as e:
        # 提取物属 root（660 root:SynologyPhotos），普通账号读不到。读不到无法判定
        # 内容，但也不能因此说文件坏了 —— 归 PENDING，把原因写清楚。
        reason = "permission denied" if isinstance(e, PermissionError) else (
            e.strerror or str(e))
        return STATUS_PENDING, "cannot read the @eaDir copy (%s)" % reason
    if len(v) != len(tail):
        return STATUS_BAD, "length mismatch jpg_tail=%d video=%d" % (len(tail), len(v))
    if hashlib.sha256(tail).digest() != hashlib.sha256(v).digest():
        return STATUS_BAD, "video content differs from the JPEG tail"

    st = os.stat(path)

    if bak and os.path.exists(bak):
        if abs(os.stat(bak).st_mtime - st.st_mtime) > 1:
            return STATUS_BAD, "mtime changed"
        delta = len(a) - os.path.getsize(bak)
        if not (DELTA_MIN <= delta <= DELTA_MAX):
            return STATUS_BAD, "size delta = %d" % delta
        why = _perm_mismatch(path, bak)
        if why:
            return STATUS_BAD, why
    return STATUS_OK, _brand(a)


def summary_status(path, server=False):
    """轻量判定，只回答"归到哪个状态"，不做逐字节比对。

    给目录级汇总用：全库跑逐字节 SHA 不现实，这里只读头部 + 查 @eaDir 存在性。
    但声明是否完整仍要验 —— 声明就在头部，不额外付 IO 代价；否则汇总视图会比
    逐文件视图宽松，把"声明残缺"的文件当成 OK。
    """
    with open(path, "rb") as f:
        head = f.read(detect.HEAD_BYTES)
        if head[:2] != b"\xff\xd8":
            return STATUS_BAD
        if xmp.has_declaration(head):
            decl = xmp.parse_declared(head)
            if not decl or any(k not in decl for k in xmp.DECL_KEYS):
                return STATUS_BAD
            if server and not os.path.exists(walk.server_video_path(path)):
                return STATUS_PENDING
            return STATUS_OK
        data = head + f.read()
    return _undeclared_status(data)[0]


def _brand(data):
    for b in mp4.BRANDS:
        if data.find(b) > 4:
            return b.decode("latin1")
    return None
