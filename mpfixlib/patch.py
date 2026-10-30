"""写入：插入 XMP、校验、失败回退。

三条硬约束（都是踩出来的）：
  1. **原地写**（r+b + truncate）保留 inode —— 群晖照片目录带 Synology ACL
     （777 + user:PhotoStation ACE），用 cp 覆盖会新建 inode，权限掉成 644、ACL 丢失。
  2. **保留 mtime** —— 否则 Synology Photos 或手机备份会判为新文件重复上传。
  3. **写后逐项校验，不符即回退**，回退本身也用原地写，避免二次破坏权限。
"""
import hashlib
import os
import shutil
import subprocess

from . import mp4, xmp

EXIV2 = os.environ.get(
    "EXIV2", "/var/packages/SynologyPhotos/target/usr/bin/exiv2")

# 期望的 GCamera 字段数：以 xmp.DECL_KEYS 为准，不另写常数
EXPECTED_XMP_FIELDS = len(xmp.DECL_KEYS)


def have_exiv2():
    """本机是否有可用 exiv2（群晖上才有；本地跑则用内置解析兜底）。"""
    return bool(EXIV2) and os.path.exists(EXIV2)


def _inplace_write(path, payload, st):
    with open(path, "r+b") as f:
        f.write(payload)
        f.truncate()
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass
    try:
        os.utime(path, (st.st_atime, st.st_mtime))
    except OSError:
        pass


def count_xmp_fields(path):
    """用 DSM 自带 exiv2 统计 Xmp.GCamera 字段数；exiv2 不可用返回 None。"""
    if not have_exiv2():
        return None
    try:
        out = subprocess.run([EXIV2, "-px", path], capture_output=True, text=True)
    except OSError:
        return None
    if out.returncode != 0:
        return None
    return sum(1 for line in out.stdout.splitlines() if "Xmp.GCamera" in line)


def apply_one(path, ctx, backup_dir=None, rel=None):
    """修补单个文件。返回 (字节增量, 警告列表)，失败抛 RuntimeError（文件已回退）。

    backup_dir 为 None 时不落备份文件；但这不影响回退能力 —— 回退用的是本次
    读进内存的原始字节，比从备份文件读回更可靠。备份只对"事后想恢复原样"
    和"进程被强杀"这两种场景有意义。
    """
    with open(path, "rb") as f:
        data = f.read()
    new = xmp.insert(data, ctx["duration_us"], ctx["offset"])

    # 自检（写入前）：尾部必须逐字节不变，偏移必须不变
    nv, _ = mp4.find_video_start(new)
    if nv is None:
        raise RuntimeError("self-check failed: no video start found after insertion")
    if hashlib.sha256(new[nv:]).hexdigest() != ctx["tail_sha"]:
        raise RuntimeError("self-check failed: MP4 tail was modified")
    if len(new) - nv != ctx["offset"]:
        raise RuntimeError("self-check failed: MicroVideoOffset changed")

    # 备份（可选，只为事后恢复）
    if backup_dir:
        bak = os.path.join(backup_dir, rel or os.path.basename(path))
        os.makedirs(os.path.dirname(bak) or ".", exist_ok=True)
        shutil.copy2(path, bak)

    st = os.stat(path)
    _inplace_write(path, new, st)

    # 校验
    warnings = []
    with open(path, "rb") as f:
        got = f.read()
    errs = []
    if len(got) != len(new):
        errs.append("size %d != %d" % (len(got), len(new)))
    if hashlib.sha256(got[nv:]).hexdigest() != ctx["tail_sha"]:
        errs.append("MP4 tail changed")
    if got.count(b"GCamera:MicroVideoOffset") != 1:
        errs.append("XMP write produced an unexpected declaration count")
    if os.stat(path).st_mode != st.st_mode:
        errs.append("permission bits changed")

    fields = count_xmp_fields(path)
    if fields is None:
        # 本地（无 exiv2）用内置解析：能解析出 7 个属性且偏移自洽即可
        fields = xmp.count_fields(got)
    if fields is None:
        warnings.append("XMP segment cannot be parsed; field count not verified")
    elif fields != EXPECTED_XMP_FIELDS:
        errs.append("exiv2 XMP field count=%d (expected %d)" % (fields, EXPECTED_XMP_FIELDS))

    if errs:
        _inplace_write(path, data, st)      # 用内存里的原始字节回退
        with open(path, "rb") as f:
            restored = hashlib.sha256(f.read()).hexdigest()
        note = "" if restored == hashlib.sha256(data).hexdigest() else \
            " (SHA mismatch after rollback - needs manual attention)"
        raise RuntimeError("verification failed, rolled back%s: %s" % (note, "; ".join(errs)))

    return len(new) - len(data), warnings
