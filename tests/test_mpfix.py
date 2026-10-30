"""mpfix 单元测试：只跑合成样本，不需要 NAS、不需要真实照片。

运行：
    python3 -m unittest discover -s tests -v
    python3 tests/test_mpfix.py
"""
import hashlib
import io
import builtins
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)

import fixtures  # noqa: E402
from mpfixlib import detect, mp4, patch, verify, walk, xmp  # noqa: E402


def _sha(b):
    return hashlib.sha256(b).hexdigest()


class _CountingFile(object):
    """包住 open() 的返回值，记录 read() 调用次数与累计字节数。

    用来证明"快判只读了头部"，而不是靠读代码猜。
    """

    def __init__(self, fh, log):
        self._fh = fh
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._fh.close()
        return False

    def read(self, n=-1):
        buf = self._fh.read(n)
        self._log["calls"].append(len(buf))
        self._log["bytes"] += len(buf)
        return buf

    def fileno(self):
        return self._fh.fileno()


def _count_reads(path, func):
    """跑一次 func(path)，统计它在这个文件上总共读了多少字节。"""
    log = {"calls": [], "bytes": 0, "verdict": None}
    real_open = builtins.open

    def fake_open(f, mode="r", *a, **kw):
        fh = real_open(f, mode, *a, **kw)
        if isinstance(fh, io.IOBase) and "b" in mode:
            return _CountingFile(fh, log)
        return fh

    with mock.patch("builtins.open", fake_open):
        log["verdict"] = func(path)[0]
    return log


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mpfix_test_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, name, data, sub=""):
        d = os.path.join(self.tmp, sub) if sub else self.tmp
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, name)
        with open(p, "wb") as f:
            f.write(data)
        return p


class TestMp4(TempDirCase):
    def test_walk_boxes(self):
        tail = fixtures.mp4_tail()
        types = [b[2] for b in mp4.walk_boxes(tail)]
        self.assertEqual(types, [b"ftyp", b"moov", b"mdat"])

    def test_duration(self):
        tail = fixtures.mp4_tail(duration_units=3000000, timescale=1000000)
        self.assertEqual(mp4.duration_us(tail), 3000000)

    def test_duration_respects_timescale(self):
        tail = fixtures.mp4_tail(duration_units=9000, timescale=3000)
        self.assertEqual(mp4.duration_us(tail), 3000000)

    def test_find_video_start(self):
        data = fixtures.jpeg() + fixtures.mp4_tail()
        vstart, brand_pos = mp4.find_video_start(data)
        self.assertEqual(data[vstart + 4:vstart + 8], b"ftyp")
        self.assertEqual(brand_pos, vstart + 4)   # "ftypmp42" 从 type 字段开始


class TestClassify(TempDirCase):
    EXPECTED = {
        "IMG_motion.jpg": "FIX",
        "IMG_static.jpg": "SKIP_NO_FTYP",
        "IMG_declared.jpg": "SKIP_ALREADY",
        "IMG_otherxmp.jpg": "SKIP_HAS_XMP",
        "IMG_badtail.jpg": "SKIP_MP4",
        "IMG_short.jpg": "SKIP_MP4",
    }

    def test_every_branch(self):
        fixtures.write_fixtures(self.tmp)
        for name, want in self.EXPECTED.items():
            got, detail, _ = detect.classify(os.path.join(self.tmp, name))
            self.assertEqual(got, want, "%s -> %s (%s)" % (name, got, detail))

    def test_isom_brand_also_accepted(self):
        name, data = fixtures.motion_photo("IMG_isom.jpg", brand=b"isom")
        p = self.put(name, data)
        verdict, _, ctx = detect.classify(p)
        self.assertEqual(verdict, "FIX")
        self.assertEqual(ctx["brand"], "ftypisom")

    def test_ctx_fields(self):
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        verdict, _, ctx = detect.classify(p)
        self.assertEqual(verdict, "FIX")
        self.assertEqual(ctx["size"], len(data))
        self.assertEqual(len(data) - ctx["vstart"], ctx["offset"])
        self.assertEqual(_sha(data[ctx["vstart"]:]), ctx["tail_sha"])

    def test_non_jpeg_extension(self):
        p = self.put("IMG.txt", b"hello")
        self.assertEqual(detect.classify(p)[0], "SKIP_EXT")

    def test_fast_path_reads_only_head(self):
        """已修补的文件只读头部就下结论，不把十几 MB 全读进来。"""
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        verdict, _, ctx = detect.classify(p)
        self.assertEqual(verdict, "FIX")
        patch.apply_one(p, ctx, self.tmp, "bk/" + name)
        patched_size = os.path.getsize(p)

        reads = _count_reads(p, detect.classify)
        self.assertEqual(reads["verdict"], "SKIP_ALREADY")
        self.assertEqual(reads["bytes"], min(patched_size, detect.HEAD_BYTES))
        self.assertEqual(len(reads["calls"]), 1)

    def test_declaration_after_head_still_detected(self):
        """XMP 落在 256KB 之后（别家工具的写法）时，退回全文判定，结论不变。"""
        name, base = fixtures.motion_photo()
        tail_len = len(base) - base.rindex(b"ftyp", 0, len(base)) + 4
        seg = xmp.build_app1(3000000, tail_len)
        padded = base[:2] + b"\x00" * (detect.HEAD_BYTES + 4096) + seg + base[2:]
        p = self.put("IMG_farxmp.jpg", padded)
        self.assertNotIn(b"GCamera:MicroVideo", padded[:detect.HEAD_BYTES])
        self.assertEqual(detect.classify(p)[0], "SKIP_ALREADY")

    def test_verdict_of_bytes_agrees_with_classify(self):
        """verify 走的是同一个判决函数，两条路径不能给出不同结论。"""
        makers = (fixtures.motion_photo, fixtures.static_photo,
                  fixtures.declared_photo, fixtures.other_xmp_photo,
                  fixtures.broken_tail_photo, fixtures.short_clip_photo)
        for maker in makers:
            name, data = maker()
            p = self.put(name, data)
            self.assertEqual(detect.classify(p)[0],
                             detect.verdict_of_bytes(data)[0], name)

    def test_verdict_of_bytes_rejects_non_jpeg(self):
        self.assertEqual(detect.verdict_of_bytes(b"not a jpeg")[0], "SKIP_NOT_JPEG")

    # --- 假 ftyp：JPEG 熵编码数据里的巧合字节 --------------------------------

    def test_fake_ftyp_in_jpeg_data_is_plain_static(self):
        """JPEG 压缩数据里偶然拼出 b"ftyp" 不该被当成 MP4。

        实测依据：2 万张纯静态照片里有 42 处这种巧合，brand 与 size 都是乱码
        （真实样本 b'ftypy\\xa1\\xc5\\xbc\\xce\\xd7\\xd1\\xc3'）。
        """
        fake = b"\x6c\xb3\x89\x75ftypy\xa1\xc5\xbc\xce\xd7\xd1\xc3"
        body = b"\x22" * 64 + fake + b"\x33" * 64
        p = self.put("IMG_fakeftyp.jpg", fixtures.jpeg(jpeg_body=body))
        self.assertEqual(detect.classify(p)[0], "SKIP_NO_FTYP")

    def test_fake_ftyp_with_readable_brand_rejected_by_size(self):
        """brand 看起来像样但 size 字段离谱（远超文件长度）时，同样不算 MP4。"""
        fake = b"\x6c\xb3\x89\x75ftypmp42" + b"\x00" * 8
        body = b"\x22" * 64 + fake + b"\x33" * 64
        p = self.put("IMG_fakeftyp2.jpg", fixtures.jpeg(jpeg_body=body))
        self.assertEqual(detect.classify(p)[0], "SKIP_NO_FTYP")

    def test_unknown_brand_with_valid_box_still_reported(self):
        """真有 MP4 结构、只是品牌不在白名单时，仍报 SKIP_BRAND 而不是当静态。

        这类文件可能是另一种动态照片，静默算作 STATIC 会让人以为永远不用处理。
        """
        data = (fixtures.jpeg() + fixtures.ftyp(brand=b"mp41")
                + fixtures.moov() + fixtures.mdat())
        p = self.put("IMG_mp41.jpg", data)
        self.assertEqual(detect.classify(p)[0], "SKIP_BRAND")


class TestXmp(unittest.TestCase):
    def test_parse_declared_roundtrip(self):
        data = xmp.insert(fixtures.jpeg() + fixtures.mp4_tail(), 3068811, 5000)
        decl = xmp.parse_declared(data)
        self.assertEqual(sorted(decl), sorted(xmp.DECL_KEYS))
        self.assertEqual(int(decl["MicroVideoOffset"]), 5000)
        self.assertEqual(int(decl["MicroVideoPresentationTimestampUs"]), 3068811)
        self.assertEqual(xmp.count_fields(data), len(xmp.DECL_KEYS))

    def test_parse_declared_none_when_absent(self):
        self.assertEqual(xmp.parse_declared(fixtures.jpeg(  # 别的 XMP 包
            payload=fixtures.xmp_segment(fixtures.XMP_OTHER))), {})
        self.assertEqual(xmp.parse_declared(fixtures.jpeg()), {})

    def test_parse_declared_none_when_truncated(self):
        seg = xmp.build_app1(3068811, 5000)
        cut = seg[:len(seg) - 20]                     # 砍掉 xpacket 结尾
        data = b"\xff\xd8" + cut + b"\x11" * 32 + b"\xff\xd9"
        self.assertEqual(xmp.parse_declared(data), {})

    def test_delta_is_about_636_bytes(self):
        data = fixtures.jpeg() + fixtures.mp4_tail()
        new = xmp.insert(data, 3000000, len(data) - 200)
        self.assertTrue(600 <= len(new) - len(data) <= 700,
                        "增量 %d 不在预期区间" % (len(new) - len(data)))

    def test_offset_unchanged_and_tail_intact(self):
        data = fixtures.jpeg() + fixtures.mp4_tail()
        vstart, _ = mp4.find_video_start(data)
        offset = len(data) - vstart
        new = xmp.insert(data, 3000000, offset)
        nv, _ = mp4.find_video_start(new)
        self.assertEqual(len(new) - nv, offset)
        self.assertEqual(_sha(new[nv:]), _sha(data[vstart:]))

    def test_declaration_detected(self):
        self.assertTrue(xmp.has_declaration(b'GCamera:MotionPhoto'))
        self.assertFalse(xmp.has_declaration(b'nothing here'))

    def test_rejects_non_jpeg(self):
        with self.assertRaises(ValueError):
            xmp.insert(b"not a jpeg", 1000, 100)


class TestPatch(TempDirCase):
    def _motion(self):
        name, data = fixtures.motion_photo()
        p = self.put(name, data, sub="src")
        verdict, _, ctx = detect.classify(p)
        self.assertEqual(verdict, "FIX")
        return p, data, ctx

    def test_apply_ok(self):
        p, data, ctx = self._motion()
        before = os.stat(p)
        delta, warns = patch.apply_one(p, ctx, self.tmp, "backup/IMG_motion.jpg")
        after = os.stat(p)

        self.assertTrue(600 <= delta <= 700, "增量 %d" % delta)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "backup", "IMG_motion.jpg")))
        with open(p, "rb") as f:
            got = f.read()
        self.assertEqual(got.count(b"GCamera:MicroVideoOffset"), 1)
        nv, _ = mp4.find_video_start(got)          # 插入后视频起点右移
        self.assertEqual(_sha(got[nv:]), ctx["tail_sha"])
        self.assertEqual(int(after.st_mtime), int(before.st_mtime), "mtime 被改动")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        # 没有 exiv2 时应改用内置解析自校验，且不因此产生警告
        if patch.count_xmp_fields(p) is None:
            self.assertEqual(warns, [], "内置解析能校验，不该再有警告")

    def test_rollback_on_verify_failure(self):
        p, data, ctx = self._motion()
        orig = _sha(data)
        with mock.patch.object(patch, "count_xmp_fields", return_value=3):
            with self.assertRaises(RuntimeError) as cm:
                patch.apply_one(p, ctx, self.tmp, "backup/IMG_motion.jpg")
        self.assertIn("rolled back", str(cm.exception))
        with open(p, "rb") as f:
            self.assertEqual(_sha(f.read()), orig, "回退后内容未还原")

    def test_apply_without_backup(self):
        """不给 backup_dir 时不落备份文件，写入照常完成。"""
        p, data, ctx = self._motion()
        delta, _ = patch.apply_one(p, ctx)
        self.assertTrue(600 <= delta <= 700, "增量 %d" % delta)
        with open(p, "rb") as f:
            self.assertEqual(f.read().count(b"GCamera:MicroVideoOffset"), 1)

    def test_rollback_works_without_backup(self):
        """回退靠的是内存里的原始字节，不是备份文件 —— 无备份也能原样还原。"""
        p, data, ctx = self._motion()
        orig = _sha(data)
        with mock.patch.object(patch, "count_xmp_fields", return_value=3):
            with self.assertRaises(RuntimeError):
                patch.apply_one(p, ctx)          # 不给 backup_dir
        with open(p, "rb") as f:
            self.assertEqual(_sha(f.read()), orig, "无备份时回退失败")

    def test_no_side_effect_when_not_called(self):
        p, data, _ = self._motion()
        self.assertEqual(_sha(open(p, "rb").read()), _sha(data))


class TestVerify(TempDirCase):
    def test_ok_when_server_video_matches(self):
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        verdict, _, ctx = detect.classify(p)
        patch.apply_one(p, ctx, self.tmp, "bk/" + name)
        # 模拟服务端：把尾部 MP4 提取到 @eaDir
        with open(p, "rb") as f:
            new = f.read()
        nv, _ = mp4.find_video_start(new)
        vdir = os.path.join(self.tmp, "@eaDir", name)
        os.makedirs(vdir, exist_ok=True)
        with open(os.path.join(vdir, "SYNOPHOTO_FILM_MOTION_PHOTO.mp4"), "wb") as f:
            f.write(new[nv:])

        st, info = verify.check_file(p, os.path.join(self.tmp, "bk", name))
        self.assertEqual(st, verify.STATUS_OK, info)

    def test_pending_when_server_video_missing(self):
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        verdict, _, ctx = detect.classify(p)
        patch.apply_one(p, ctx, self.tmp, "bk/" + name)
        st, info = verify.check_file(p)
        self.assertEqual(st, verify.STATUS_PENDING)
        self.assertIn("not extracted", info)

    def test_missing_when_not_yet_patched(self):
        """缺声明的华为动态照片是"待修"，不能和静态照片混为一谈。"""
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        self.assertEqual(verify.check_file(p)[0], verify.STATUS_MISSING)
        self.assertEqual(verify.check_local(p)[0], verify.STATUS_MISSING)

    def test_bad_when_structure_broken(self):
        """有 ftyp 但 box 链/时长说不通，说明像动态照片却坏了，算 BAD 而不是 STATIC。"""
        p = self.put(*fixtures.broken_tail_photo())
        self.assertEqual(verify.check_local(p)[0], verify.STATUS_BAD)
        p2 = self.put(*fixtures.short_clip_photo())
        self.assertEqual(verify.check_local(p2)[0], verify.STATUS_BAD)

    def test_summary_status_is_lightweight(self):
        """--summary 走轻量判定：未修的动态照片归 MISSING，修好的归 OK。"""
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        self.assertEqual(verify.summary_status(p, server=True), verify.STATUS_MISSING)

        verdict, _, ctx = detect.classify(p)
        patch.apply_one(p, ctx)
        self.assertEqual(verify.summary_status(p, server=False), verify.STATUS_OK)
        # 服务端模式且没有 @eaDir -> PENDING
        self.assertEqual(verify.summary_status(p, server=True), verify.STATUS_PENDING)

    def test_static_counted_separately(self):
        name, data = fixtures.static_photo()
        p = self.put(name, data)
        self.assertEqual(verify.check_file(p)[0], verify.STATUS_STATIC)

    def _server_extract(self, p):
        """模拟群晖：把文件尾部 MP4 提取到 @eaDir 下，返回提取物路径。"""
        name = os.path.basename(p)
        with open(p, "rb") as f:
            new = f.read()
        nv, _ = mp4.find_video_start(new)
        vdir = os.path.join(os.path.dirname(p), "@eaDir", name)
        os.makedirs(vdir, exist_ok=True)
        vp = os.path.join(vdir, "SYNOPHOTO_FILM_MOTION_PHOTO.mp4")
        with open(vp, "wb") as f:
            f.write(new[nv:])
        return vp

    def test_ok_when_permission_is_644(self):
        """644 是普通照片目录的常态。不是 777 不代表文件坏了 —— 早先这里误报过。"""
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        if os.name == "posix":
            os.chmod(p, 0o644)
        _, _, ctx = detect.classify(p)
        patch.apply_one(p, ctx)
        self._server_extract(p)
        st, info = verify.check_file(p)
        self.assertEqual(st, verify.STATUS_OK, info)

    def test_pending_when_server_copy_is_unreadable(self):
        """@eaDir 提取物属 root（660），普通账号读不到 —— 读不到不等于坏了。"""
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        _, _, ctx = detect.classify(p)
        patch.apply_one(p, ctx)
        vp = self._server_extract(p)
        real_open = builtins.open

        def fake_open(f, mode="r", *a, **kw):
            if os.fspath(f) == vp:
                raise PermissionError(13, "Permission denied", vp)
            return real_open(f, mode, *a, **kw)

        with mock.patch("builtins.open", fake_open):
            st, info = verify.check_file(p)
        self.assertEqual(st, verify.STATUS_PENDING, info)
        self.assertIn("permission denied", info)

    def test_bad_when_permission_changed_against_backup(self):
        """有基线时仍要能抓到"权限被改了" —— 比的是和备份一致，不是绝对值。"""
        if os.name != "posix":
            self.skipTest("permission bits are POSIX only")
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        _, _, ctx = detect.classify(p)
        patch.apply_one(p, ctx, self.tmp, "bk/" + name)
        self._server_extract(p)
        bak = os.path.join(self.tmp, "bk", name)
        os.chmod(p, 0o640)
        st, info = verify.check_file(p, bak)
        self.assertEqual(st, verify.STATUS_BAD, info)
        self.assertIn("permission bits changed", info)


class TestVerifyLocal(TempDirCase):
    """离线校验：不依赖 @eaDir，任何机器都能跑（本地修完准备上传时用）。"""

    def _patched(self, with_backup=True):
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        verdict, _, ctx = detect.classify(p)
        self.assertEqual(verdict, "FIX")
        bak = os.path.join(self.tmp, "bk", name)
        patch.apply_one(p, ctx, os.path.join(self.tmp, "bk"), name)
        return p, bak if with_backup else None

    def test_ok_after_patch(self):
        p, bak = self._patched()
        st, info = verify.check_local(p, bak)
        self.assertEqual(st, verify.STATUS_OK, info)
        self.assertEqual(info, "ftypmp42")

    def test_static(self):
        p = self.put(*fixtures.static_photo())
        self.assertEqual(verify.check_local(p)[0], verify.STATUS_STATIC)

    def test_fake_ftyp_is_static_not_bad(self):
        """JPEG 数据里巧合出现的 ftyp 曾被报成 BAD（误报）。

        那类文件里其实连 GCamera 声明都没有（probe 实测 xmp_ns=-1、GCamera=-1），
        报 BAD 会让人以为照片坏了；正确答案是"本来就不是动态照片"。
        """
        fake = b"\x6c\xb3\x89\x75ftypy\xa1\xc5\xbc\xce\xd7\xd1\xc3"
        body = b"\x22" * 64 + fake + b"\x33" * 64
        p = self.put("IMG_fakeftyp.jpg", fixtures.jpeg(jpeg_body=body))
        self.assertEqual(verify.check_local(p)[0], verify.STATUS_STATIC)
        self.assertEqual(verify.check_file(p)[0], verify.STATUS_STATIC)

    def test_non_jpeg_with_jpeg_extension_is_bad(self):
        """扩展名是 .jpg 但内容不是 JPEG：summary 早就报 BAD，逐文件却报 STATIC。

        实测样本是一张 PNG 存成了 .jpeg（首字节 89 50 4E 47）。两种视图必须一致，
        而且都该算 BAD —— 算 STATIC 等于把它藏进"不需要处理的照片"里。
        """
        p = self.put("IMG_png_named_jpeg.jpeg", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        self.assertEqual(verify.check_local(p)[0], verify.STATUS_BAD)
        self.assertEqual(verify.summary_status(p, server=False), verify.STATUS_BAD)

    def test_bad_when_declared_offset_wrong(self):
        name, data = fixtures.motion_photo()
        vstart, _ = mp4.find_video_start(data)
        # 声明一个比真实尾长大的偏移：群晖按它截会截出废数据
        wrong = data[:2] + xmp.build_app1(3068811, len(data) - vstart + 10) + data[2:]
        p = self.put("IMG_wrong_offset.jpg", wrong)
        st, info = verify.check_local(p)
        self.assertEqual(st, verify.STATUS_BAD)
        self.assertIn("declared offset", info)

    def test_bad_when_mtime_touched(self):
        p, bak = self._patched()
        os.utime(bak, (os.path.getmtime(bak) - 100, os.path.getmtime(bak) - 100))
        st, info = verify.check_local(p, bak)
        self.assertEqual(st, verify.STATUS_BAD)
        self.assertIn("mtime", info)

    def test_bad_when_tail_differs_from_backup(self):
        p, bak = self._patched()
        with open(bak, "rb") as f:
            b = bytearray(f.read())
        b[-1] ^= 0xFF
        with open(bak, "wb") as f:
            f.write(b)
        st, info = verify.check_local(p, bak)
        self.assertEqual(st, verify.STATUS_BAD)
        self.assertIn("MP4 tail", info)


class TestWalk(TempDirCase):
    def test_resolve_targets_single_file(self):
        name, data = fixtures.motion_photo()
        p = self.put(name, data)
        root, files = walk.resolve_targets(p)
        self.assertEqual(os.path.basename(root), os.path.basename(self.tmp))
        self.assertEqual(files, [p])

    def test_resolve_targets_dir(self):
        fixtures.write_fixtures(self.tmp)
        root, files = walk.resolve_targets(self.tmp)
        self.assertEqual(os.path.abspath(root), os.path.abspath(self.tmp))
        self.assertEqual(len(files), len(fixtures.ALL_FIXTURES))
        self.assertTrue(all(f.lower().endswith((".jpg", ".jpeg")) for f in files))

    def test_resolve_targets_rejects_missing_and_non_jpeg(self):
        self.assertIsNone(walk.resolve_targets(os.path.join(self.tmp, "nope.jpg")))
        self.assertIsNone(walk.resolve_targets(self.put("note.txt", b"hi")))

    def test_iter_jpg_dirs_skips_thumbdir_and_skip_list(self):
        subs = ["DEV-A/2026/01", "DEV-A/2026/02", "DEV-B/@eaDir", ""]
        for sub in subs:
            self.put("IMG_%s.jpg" % (sub.replace("/", "_") or "root"), b"\xff\xd8x", sub=sub)
        rows = dict(walk.iter_jpg_dirs(self.tmp))
        self.assertIn("DEV-A/2026/01", rows)
        self.assertNotIn("DEV-B/@eaDir", rows)
        rows2 = dict(walk.iter_jpg_dirs(self.tmp, skip={"DEV-A/2026/02"}))
        self.assertNotIn("DEV-A/2026/02", rows2)

    def test_parse_skip(self):
        self.assertEqual(walk.parse_skip("a/b, c/d ,"), {"a/b", "c/d"})
        self.assertEqual(walk.parse_skip(""), set())

    # --- iter_dirs_recent：按目录 mtime 挑增量目录 ---------------------------

    def _age(self, sub, seconds):
        """把目录 mtime 拨回到 seconds 秒之前（模拟"上次上传是多久以前"）。"""
        p = os.path.join(self.tmp, sub) if sub else self.tmp
        t = time.time() - seconds
        os.utime(p, (t, t))
        return t

    def test_iter_dirs_recent_filters_by_dir_mtime(self):
        now = time.time()
        self.put("a.jpg", b"\xff\xd8x", sub="fresh")
        self.put("b.jpg", b"\xff\xd8x", sub="old")
        self._age("fresh", 3600)
        self._age("old", 10 * 86400)
        rels = [r[0] for r in walk.iter_dirs_recent(self.tmp, since_days=2, min_age_s=120, now=now)]
        self.assertEqual(rels, ["fresh"])

    def test_iter_dirs_recent_respects_min_age(self):
        """目录 mtime 太新 = 可能正在上传，这一轮先不碰。"""
        now = time.time()
        self.put("a.jpg", b"\xff\xd8x", sub="uploading")
        self._age("uploading", 10)
        self.assertEqual(walk.iter_dirs_recent(self.tmp, since_days=2, min_age_s=120, now=now), [])
        rels = [r[0] for r in walk.iter_dirs_recent(self.tmp, since_days=2, min_age_s=0, now=now)]
        self.assertEqual(rels, ["uploading"])

    def test_iter_dirs_recent_zero_since_means_no_lower_bound(self):
        now = time.time()
        self.put("a.jpg", b"\xff\xd8x", sub="ancient")
        self._age("ancient", 3650 * 86400)
        rels = [r[0] for r in walk.iter_dirs_recent(self.tmp, since_days=0, min_age_s=0, now=now)]
        self.assertEqual(rels, ["ancient"])

    def test_iter_dirs_recent_counts_jpgs_and_ignores_others(self):
        now = time.time()
        d = os.path.join(self.tmp, "mixed")
        os.makedirs(d)
        for n in ("a.jpg", "b.JPEG", "c.txt", ".hidden.jpg", "d.mp4"):
            with open(os.path.join(d, n), "wb") as f:
                f.write(b"\xff\xd8x")
        self._age("mixed", 3600)
        rows = walk.iter_dirs_recent(self.tmp, since_days=2, min_age_s=120, now=now)
        self.assertEqual([r[0] for r in rows], ["mixed"])
        self.assertEqual(rows[0][1], 2)
        self.assertAlmostEqual(rows[0][2], now - 3600, delta=5)

    def test_iter_dirs_recent_prunes_thumbdir(self):
        now = time.time()
        self.put("a.jpg", b"\xff\xd8x", sub="d1/@eaDir")
        self._age("d1/@eaDir", 3600)
        self._age("d1", 3600)
        self.assertEqual(walk.iter_dirs_recent(self.tmp, since_days=2, min_age_s=120, now=now), [])

    def test_iter_dirs_recent_skips_subtree(self):
        now = time.time()
        self.put("a.jpg", b"\xff\xd8x", sub="keep")
        self.put("b.jpg", b"\xff\xd8x", sub="drop/deep")
        self._age("keep", 3600)
        self._age("drop/deep", 3600)
        self._age("drop", 3600)
        rels = [r[0] for r in walk.iter_dirs_recent(
            self.tmp, since_days=2, min_age_s=120, skip={"drop"}, now=now)]
        self.assertEqual(rels, ["keep"])

    def test_iter_dirs_recent_root_is_dot(self):
        now = time.time()
        self.put("root.jpg", b"\xff\xd8x")
        self._age("", 3600)
        rels = [r[0] for r in walk.iter_dirs_recent(self.tmp, since_days=2, min_age_s=120, now=now)]
        self.assertEqual(rels, ["."])

    def test_iter_dirs_recent_shares_skip_parsing_with_env_style_spec(self):
        self.assertEqual(walk.parse_skip("DEV-A/2026/01,DEV-A/2026/02"),
                         {"DEV-A/2026/01", "DEV-A/2026/02"})

    def test_recent_lower_bound_tracks_min_age(self):
        """下界与上界一起平移 min_age_s，窗口宽度才等于 since_days 天。

        若只把上界减掉 min_age_s、下界仍从 now 直接减，窗口就短了 120 秒，
        两次运行之间留一个永久漏掉的缝。这个用例专门锁住这点：
        24h01m 前的目录必须落在 --recent 1 的窗口内。
        """
        now = time.time()
        self.put("a.jpg", b"\xff\xd8x", sub="inside")
        self.put("a.jpg", b"\xff\xd8x", sub="outside")
        t_in = now - 86400 - 60               # 24h01m 前：旧实现会漏，新实现要覆盖
        t_out = now - 86400 - 300             # 24h05m 前：两天窗口都够不着，应排除
        os.utime(os.path.join(self.tmp, "inside"), (t_in, t_in))
        os.utime(os.path.join(self.tmp, "outside"), (t_out, t_out))
        rels = [r[0] for r in walk.iter_dirs_recent(
            self.tmp, since_days=1, min_age_s=120, now=now)]
        self.assertEqual(rels, ["inside"])

    def test_recent_daily_runs_tile_without_gap(self):
        """每天同一时刻跑 --recent 1，相邻两轮窗口首尾相接：不漏，也不重。

        构造一批 mtime 连续分布的目录，让第一轮（昨天此刻跑到此刻）与
        第二轮（此刻跑到明天此刻）各自挑选，合并后必须覆盖整个区间。
        """
        now0 = time.time()
        marks = []
        for i in range(-17, 10):              # mtime 从 -51h 到 +27h，每 3 小时一个
            name = "d%+03d" % i
            d = os.path.join(self.tmp, name)
            os.makedirs(d)
            with open(os.path.join(d, "a.jpg"), "wb") as f:
                f.write(b"\xff\xd8x")
            t = now0 + i * 3 * 3600
            os.utime(d, (t, t))
            marks.append((name, t))

        def picked(now):
            return {r[0] for r in walk.iter_dirs_recent(
                self.tmp, since_days=1, min_age_s=120, now=now)}

        first = picked(now0)
        second = picked(now0 + 86400)
        both = first | second

        # 两轮合并后的理论覆盖区间：[now0-1天-120s, now0+1天-120s]
        lo, hi = now0 - 86400 - 120, now0 + 86400 - 120
        for name, t in marks:
            if lo <= t <= hi:
                self.assertIn(name, both, "落在窗口内却被两轮都漏掉: %s" % name)

        # 衔接确实靠第二轮：第一轮单独覆盖不到它之后的部分
        after = [n for n, t in marks if t > now0 - 120 and t <= hi]
        self.assertTrue(after, "样本没有覆盖到第二轮的范围，用例本身失效")
        for n in after:
            self.assertIn(n, second)
            self.assertNotIn(n, first)


class TestCli(TempDirCase):
    def _run(self, *argv):
        return subprocess.run([sys.executable, os.path.join(REPO, "mpfix")] + list(argv),
                              capture_output=True, text=True, cwd=REPO)

    def test_version(self):
        r = self._run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("mpfix", r.stdout)

    def test_removed_commands_are_gone(self):
        """入口收敛成两个：scan / tree / index / plan 都不该再被接受。"""
        for gone in ("scan", "tree", "index", "plan"):
            r = self._run(gone, self.tmp)
            self.assertEqual(r.returncode, 2, gone)
            self.assertIn("invalid choice", r.stderr)

    # --- 单文件目标：clone 下来修一张图就上传的场景 ---

    def test_apply_single_file(self):
        p = self.put(*fixtures.motion_photo())
        before = _sha(open(p, "rb").read())
        r = self._run("apply", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RESULT: ALL_OK", r.stdout)
        self.assertIn("upload", r.stdout)
        after = open(p, "rb").read()
        self.assertNotEqual(_sha(after), before)
        self.assertEqual(after.count(b"GCamera:MicroVideoOffset"), 1)

    def test_apply_single_static_file_is_noop(self):
        p = self.put(*fixtures.static_photo())
        before = _sha(open(p, "rb").read())
        r = self._run("apply", p)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("SKIP_NO_FTYP", r.stdout)
        self.assertEqual(_sha(open(p, "rb").read()), before)

    def test_verify_offline_single_file(self):
        p = self.put(*fixtures.motion_photo())
        self._run("apply", p)
        r = self._run("verify", p, "--offline")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RESULT: ALL_VERIFIED", r.stdout)
        self.assertIn("offline", r.stdout)

    def test_apply_rejects_non_existent_path(self):
        r = self._run("apply", os.path.join(self.tmp, "ghost.jpg"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("Path does not exist", r.stdout)

    # --- 范围：默认单层，--tree 递归，--recent 增量 ---

    def test_apply_dir_is_not_recursive_by_default(self):
        name, data = fixtures.motion_photo()
        p = self.put(name, data, sub="sub")
        r = self._run("apply", self.tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("jpg files: 0", r.stdout)
        self.assertEqual(open(p, "rb").read(), data, "子目录不该被处理")

    def test_apply_tree_recurses_and_mirrors_backup(self):
        name, data = fixtures.motion_photo()
        sub = os.path.join(self.tmp, "2026", "10")
        os.makedirs(sub)
        p = os.path.join(sub, name)
        with open(p, "wb") as f:
            f.write(data)
        bk = os.path.join(self.tmp, "_bk")
        r = self._run("apply", self.tmp, "--tree", "--backup-dir", bk)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("RESULT: ALL_OK", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(bk, "2026", "10", name)),
                        "备份应按相对路径镜像")
        self.assertEqual(open(p, "rb").read().count(b"GCamera:MicroVideoOffset"), 1)

    def test_apply_tree_without_backup_warns_but_still_writes(self):
        """不备份不等于不写：回退靠内存里的原始字节，所以照常修，只打一行警告。"""
        name, data = fixtures.motion_photo()
        p = self.put(name, data, sub="sub")
        r = self._run("apply", self.tmp, "--tree")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("nothing will be backed up", r.stdout)
        self.assertEqual(open(p, "rb").read().count(b"GCamera:MicroVideoOffset"), 1)

    def test_recent_and_tree_are_mutually_exclusive(self):
        r = self._run("apply", self.tmp, "--tree", "--recent", "2")
        self.assertEqual(r.returncode, 2)
        self.assertIn("not allowed with", r.stderr)

    def test_apply_recent_skips_untouched_dirs(self):
        name, data = fixtures.motion_photo()
        old = self.put(name, data, sub="old")
        new = self.put(name, data, sub="new")
        # old 十几天没动过 -> 不在 --recent 2 的范围
        # new 十分钟前有变更 -> 命中，但不能太新（内建 120 秒阈值防"正在上传"）
        t_old = time.time() - 10 * 86400
        t_new = time.time() - 600
        os.utime(os.path.join(self.tmp, "old"), (t_old, t_old))
        os.utime(os.path.join(self.tmp, "new"), (t_new, t_new))
        r = self._run("apply", self.tmp, "--recent", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(open(old, "rb").read(), data, "10 天没动过的目录不该被碰")
        self.assertEqual(open(new, "rb").read().count(b"GCamera:MicroVideoOffset"), 1)

    def test_recent_skips_dirs_that_are_too_new(self):
        """刚变更的目录可能还在上传，先放过，留给下一轮。"""
        name, data = fixtures.motion_photo()
        p = self.put(name, data, sub="fresh")
        r = self._run("apply", self.tmp, "--recent", "2")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(open(p, "rb").read(), data, "太新的目录本轮应跳过")
        self.assertIn("directories: 0", r.stdout)

    # --- verify ---

    def test_verify_empty_dir_exits_2(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        r = self._run("verify", empty)
        self.assertEqual(r.returncode, 2)
        self.assertIn("No .jpg files found", r.stdout)

    def test_verify_dir_is_not_recursive_by_default(self):
        name, data = fixtures.motion_photo()
        self.put(name, data, sub="sub")
        r = self._run("verify", self.tmp, "--offline")
        self.assertEqual(r.returncode, 2)
        self.assertIn("No .jpg files found", r.stdout)

    def test_verify_reports_missing_before_any_fix(self):
        name, data = fixtures.motion_photo()
        self.put(name, data)
        r = self._run("verify", self.tmp, "--offline")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("MISSING", r.stdout)
        self.assertIn("MISSING the declaration", r.stdout)

    def test_verify_server_mode_reports_pending(self):
        """本机没有 @eaDir 时，修好的文件是 PENDING（等索引），不是 BAD。"""
        p = self.put(*fixtures.motion_photo())
        self._run("apply", p)
        r = self._run("verify", p)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PENDING", r.stdout)
        self.assertIn("PENDING", r.stdout)

    def test_verify_tree_after_apply(self):
        name, data = fixtures.motion_photo()
        self.put(name, data, sub="2026")
        self._run("apply", self.tmp, "--tree", "--backup-dir", os.path.join(self.tmp, "_bk"))
        r = self._run("verify", self.tmp, "--tree", "--offline")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("RESULT: ALL_VERIFIED", r.stdout)

    def test_verify_summary_reports_five_states(self):
        self.put(*fixtures.motion_photo())
        self.put(*fixtures.static_photo())
        r = self._run("verify", self.tmp, "--offline", "--summary")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for col in ("OK", "STATIC", "MISSING", "PENDING", "BAD"):
            self.assertIn(col, r.stdout)
        self.assertIn("no byte-level comparison", r.stdout)
        self.assertIn("RESULT: ALL_VERIFIED", r.stdout)

    def test_verify_summary_flags_incomplete_declaration(self):
        """汇总视图不能比逐文件视图宽松：声明残缺的文件在两边都要报 BAD。"""
        self.put(*fixtures.declared_photo())
        r = self._run("verify", self.tmp, "--offline", "--summary")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("structurally broken", r.stdout)

    def test_verify_summary_flags_broken_files(self):
        self.put(*fixtures.broken_tail_photo())
        r = self._run("verify", self.tmp, "--offline", "--summary")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("structurally broken", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
