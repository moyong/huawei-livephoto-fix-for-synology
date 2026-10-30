"""命令行入口：mpfix apply / mpfix verify。

    mpfix apply  <文件或目录>                     修单张或单层目录（会写文件）
    mpfix apply  <目录> --tree                    递归修整树
    mpfix apply  <目录> --recent 2                只修最近 2 天内变更的目录
    mpfix verify <文件或目录> [--offline]         看状态，永远不写文件
    mpfix verify <目录> --tree --summary          按目录汇总五状态

两个命令职责互斥：apply 是唯一会写文件的，verify 是唯一只读的。
范围开关 --tree / --recent 对两者含义相同，且都隐含递归；两者互斥。

界面文案一律英文（开源面向国际用户）；代码注释保留中文。
"""
import argparse
import os
import sys
from collections import Counter

from . import __version__, detect, patch, verify, walk

LINE = "-" * 78

# 逐文件统计的列顺序
STATUS_COLS = (verify.STATUS_OK, verify.STATUS_STATIC, verify.STATUS_MISSING,
               verify.STATUS_PENDING, verify.STATUS_BAD)


def _rel(root, path):
    """相对 root 的路径，统一成 / 分隔（备份镜像与 --skip 都按这个比较）。"""
    return os.path.relpath(path, root).replace(os.sep, "/")


def _dirpath(root, rel):
    """把 / 分隔的相对路径拼回本地路径，避免 Windows 上出现 root\\2026/09 这种混写。"""
    if rel in (".", ""):
        return root
    return os.path.join(root, *rel.split("/"))


def run_dir(dirpath, out, backup_dir=None, make_allow=None, root=None, files=None,
            verbose=True):
    """处理一批文件：判定 + 写入。返回统计 dict。

    verbose=True  打逐文件明细与分类汇总（单文件 / 单层目录用）
    verbose=False 不打明细，留给调用方打紧凑进度行（递归用）
    """
    root = root or dirpath
    if files is None:
        files = [os.path.join(dirpath, f) for f in walk.list_jpgs(dirpath)]

    if verbose:
        out.write("Target: %s\n" % dirpath)
        out.write("jpg files: %d\n" % len(files))
        out.write(LINE + "\n")

    buckets = Counter()
    makes = Counter()
    todo = []
    for p in files:
        try:
            verdict, detail, ctx = detect.classify(p)
        except Exception as e:                      # noqa: BLE001 - 单文件异常不影响整批
            verdict, detail, ctx = "ERROR", str(e), None
        mk = (ctx or {}).get("make") or "<none>"
        makes[mk] += 1
        if verdict == "FIX" and make_allow and mk.upper() not in make_allow:
            verdict, detail = ("SKIP_MAKE",
                               "make %s is not in the allow-list %s" % (mk, sorted(make_allow)))
        buckets[verdict] += 1
        if verdict == "FIX":
            todo.append((p, ctx))
            if verbose:
                out.write("  [FIX] %-32s %7.2fMB  %-7s %-22s gap=%-6d %.3fs off=%d\n"
                          % (os.path.basename(p), ctx["size"] / 1048576.0, ctx["make"] or "-",
                             (ctx["model"] or "-")[:22], ctx["gap"], ctx["duration_us"] / 1e6,
                             ctx["offset"]))
        elif verbose:
            out.write("  [%s] %-32s %-7s %s\n"
                      % (verdict, os.path.basename(p), mk, detail))

    stats = {"files": len(files), "fix": len(todo), "ok": 0, "fail": 0,
             "buckets": buckets, "fails": []}

    if verbose:
        out.write(LINE + "\n")
        out.write("Verdicts:\n")
        for k in sorted(buckets):
            out.write("   %-16s %d\n" % (k, buckets[k]))
        out.write("Makes: %s\n" % ", ".join(
            "%s=%d" % (k, v) for k, v in makes.most_common()))
        out.write("Patchable: %d file(s), ~%d bytes to be added\n"
                  % (len(todo), len(todo) * 636))

    if not todo:
        return stats

    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        if verbose:
            out.write("\nBackup dir: %s\n" % backup_dir)

    for p, ctx in todo:
        rel = _rel(root, p)
        try:
            delta, warns = patch.apply_one(p, ctx, backup_dir, rel)
            stats["ok"] += 1
            if verbose:
                out.write("  OK   %s  (+%d B)%s\n"
                          % (os.path.basename(p), delta,
                             "  [" + "; ".join(warns) + "]" if warns else ""))
        except Exception as e:                      # noqa: BLE001 - 失败已回退，继续下一个
            stats["fail"] += 1
            stats["fails"].append((p, str(e)))
            out.write("  FAIL %s  %s\n" % (p, e))    # 失败一律打完整路径

    if verbose:
        out.write(LINE + "\n")
        out.write("patched %d / failed %d\n" % (stats["ok"], stats["fail"]))
    return stats


def _resolve(args):
    """解析目标：返回 (target, root, files, 是否单文件)。无法解析时 root 为 None。"""
    target = os.path.abspath(args.dir).rstrip(os.sep) or os.sep
    got = walk.resolve_targets(target)
    if got is None:
        return target, None, None, False
    root, files = got
    return target, root, files, os.path.isfile(target)


def _not_found(target):
    print("Path does not exist, or is not a .jpg/.jpeg file: %s" % target)
    print("(apply / verify accept a single .jpg/.jpeg file or a directory)")


def _allow(args):
    v = getattr(args, "make_allow", None)
    return {x.strip().upper() for x in v.split(",") if x.strip()} if v else None


def _skip(args):
    return walk.parse_skip(args.skip) | walk.parse_skip(os.environ.get("MP_SKIP"))


def _recursive(args):
    return bool(args.tree) or args.recent is not None


def _scope_text(args, single):
    if single:
        return "single file"
    if args.recent is not None:
        return "last %g days" % args.recent
    return "recursive" if args.tree else "one level"


# ---------------------------------------------------------------- apply

def cmd_apply(args):
    target, root, files, single = _resolve(args)
    if root is None:
        _not_found(target)
        return 2

    backup_dir = os.path.abspath(args.backup_dir) if args.backup_dir else None

    if not _recursive(args):
        stats = run_dir(target, sys.stdout, backup_dir=backup_dir,
                        make_allow=_allow(args), root=root, files=files)
        if stats["fail"]:
            if backup_dir:
                print("Failures occurred; backup dir kept: %s" % backup_dir)
            return 1
        if stats["fix"]:
            print("RESULT: ALL_OK")
            if single:
                print("Next: upload this file to Synology Photos; "
                      "the LIVE badge appears once DSM has indexed it.")
        return 0

    if not backup_dir:
        print("!! Recursive write into %s with no --backup-dir: "
              "nothing will be backed up" % root)

    skip = _skip(args)
    dirs = walk.iter_target_dirs(root, skip, args.recent)
    total = sum(n for _, n in dirs)
    print("Mode: %s   directories: %d   jpg files: %d"
          % ("whole tree" if args.recent is None else "last %g days" % args.recent,
             len(dirs), total))
    if skip:
        print("Skipping: %s" % ", ".join(sorted(skip)))
    print(LINE)

    tfix = tok = tfail = 0
    all_fails = []
    for idx, (rel, cnt) in enumerate(dirs, 1):
        dirpath = _dirpath(root, rel)
        # 备份路径由 run_dir 用"相对总根"的路径拼，这里不要预先加上 rel，
        # 否则会嵌套成 backup/2026/10/2026/10/IMG.jpg
        stats = run_dir(dirpath, sys.stdout, backup_dir=backup_dir, make_allow=_allow(args),
                        root=root, verbose=False)
        tfix += stats["fix"]
        tok += stats["ok"]
        tfail += stats["fail"]
        all_fails.extend(stats["fails"])
        print("[%d/%d] %-52s jpg=%-5d fix=%-5d ok=%-5d fail=%d%s"
              % (idx, len(dirs), rel, cnt, stats["fix"], stats["ok"], stats["fail"],
                 "  <-- FAILURES" if stats["fail"] else ""), flush=True)

    print(LINE)
    print("Total: patchable %d / patched %d / failed %d" % (tfix, tok, tfail))
    if all_fails:
        print("\nFailures:")
        for p, why in all_fails:
            print("   %s  %s" % (p, why))
        return 1
    if backup_dir:
        print("Backup: %s" % backup_dir)
    print("RESULT: ALL_OK")
    return 0


# ---------------------------------------------------------------- verify

def _collect(root, files, recursive, skip, recent):
    """收集待检查的文件路径。非递归时就是单文件或单层目录的内容。"""
    if not recursive:
        return list(files)
    out = []
    for rel, _ in walk.iter_target_dirs(root, skip, recent):
        dirpath = _dirpath(root, rel)
        for name in walk.list_jpgs(dirpath):
            out.append(os.path.join(dirpath, name))
    return out


def _print_summary(rows, root, server):
    """按目录汇总五状态。轻量判定，不做逐字节比对。"""
    groups = {}
    for p in rows:
        d = os.path.dirname(p)
        rel = _rel(root, d) if d != root else "."
        groups.setdefault(rel, Counter())[verify.summary_status(p, server)] += 1

    print("%-40s %8s %8s %9s %8s %6s" % (
        "DIRECTORY", verify.STATUS_OK, verify.STATUS_STATIC,
        verify.STATUS_MISSING, verify.STATUS_PENDING, verify.STATUS_BAD))
    print(LINE)
    total = Counter()
    for rel in sorted(groups):
        g = groups[rel]
        total.update(g)
        print("%-40s %8d %8d %9d %8d %6d"
              % (rel[:40], g[verify.STATUS_OK], g[verify.STATUS_STATIC],
                 g[verify.STATUS_MISSING], g[verify.STATUS_PENDING],
                 g[verify.STATUS_BAD]))
    print(LINE)
    print("%-40s %8d %8d %9d %8d %6d"
          % ("TOTAL", total[verify.STATUS_OK], total[verify.STATUS_STATIC],
             total[verify.STATUS_MISSING], total[verify.STATUS_PENDING],
             total[verify.STATUS_BAD]))
    print("\nOK here only means %s; no byte-level comparison was done. "
          "Drop --summary for strict verification."
          % ("the XMP declaration is complete" if not server
             else "the XMP declaration is complete and @eaDir has the video"))
    return total


def cmd_verify(args):
    target, root, files, single = _resolve(args)
    if root is None:
        _not_found(target)
        return 2

    server = not args.offline
    rows = _collect(root, files, _recursive(args), _skip(args), args.recent)

    print("Mode: %s   scope: %s   target: %s"
          % ("offline (no Synology needed)" if args.offline
             else "server-side (compares against @eaDir)",
             _scope_text(args, single), target))

    if not rows:
        print("\n!! No .jpg files found - wrong path, or --skip excluded everything")
        return 2

    if args.summary:
        total = _print_summary(rows, root, server)
        bad = total[verify.STATUS_BAD]
        if bad:
            print("\n%d file(s) are structurally broken; drop --summary for details" % bad)
            return 1
        print("\nRESULT: ALL_VERIFIED")
        return 0

    check = verify.check_local if args.offline else verify.check_file
    stats = Counter()
    problems = []
    for p in rows:
        try:
            st, info = check(p)
        except Exception as e:                      # noqa: BLE001
            st, info = verify.STATUS_BAD, "error: %s" % e
        stats[st] += 1
        if st == verify.STATUS_BAD:
            problems.append((p, info))

    for col in STATUS_COLS:
        print("%-10s %8d" % (col, stats[col]))
    print(LINE)
    print("%-10s %8d" % ("TOTAL", sum(stats.values())))

    if stats[verify.STATUS_MISSING] and not single:
        print("\n%d file(s) MISSING the declaration: run mpfix apply to fix them"
              % stats[verify.STATUS_MISSING])
    if stats[verify.STATUS_PENDING]:
        print("%d file(s) PENDING: no readable @eaDir copy yet - Synology has not "
              "extracted the video, or the copy is root-only. Just wait." %
              stats[verify.STATUS_PENDING])
    if problems:
        print("\nProblems (up to 30):")
        for n, why in problems[:30]:
            print("   %-70s %s" % (n, why))
        if len(problems) > 30:
            print("   ... and %d more" % (len(problems) - 30))
        if server:
            print("\nHint: if these files have no @eaDir extraction, "
                  "run with --offline on a local machine")
        return 1

    print("\nRESULT: ALL_VERIFIED")
    if single:
        if stats[verify.STATUS_MISSING]:
            print("This file is MISSING the XMP declaration - run mpfix apply on it.")
        elif stats[verify.STATUS_OK]:
            print("This file is ready to upload to Synology Photos.")
        elif stats[verify.STATUS_PENDING]:
            print("Patched correctly, but the video is not verifiable yet: Synology has "
                  "not extracted it, or its @eaDir copy is not readable. Wait for "
                  "indexing, then run this again (or use --offline).")
        else:
            print("This is not a Motion Photo that needs fixing; leave it alone.")
    return 0


# ---------------------------------------------------------------- parser

def _range_opts(p):
    g = p.add_mutually_exclusive_group()
    g.add_argument("--tree", action="store_true", help="recurse into the whole subtree")
    g.add_argument("--recent", type=float, metavar="DAYS",
                   help="recurse, but only into directories changed in the last DAYS days")


def build_parser():
    p = argparse.ArgumentParser(
        prog="mpfix",
        description="Add a Google Motion Photo XMP declaration to Huawei Live Photos "
                    "so that Synology Photos recognises them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  Patch one file (works on your PC; upload it afterwards):\n"
               "    mpfix apply  ~/Desktop/IMG_001.jpg\n"
               "    mpfix verify ~/Desktop/IMG_001.jpg --offline\n"
               "  Patch one directory (not recursive):\n"
               "    mpfix apply  ~/Pictures/Camera --backup-dir ~/mpfix_bk\n"
               "  Patch a whole tree on the NAS:\n"
               "    mpfix apply  /volume1/photo --tree --backup-dir /volume1/mpfix_bk\n"
               "    mpfix verify /volume1/photo --tree --summary\n"
               "  Scheduled task: only handle newly uploaded directories\n"
               "    mpfix apply  /volume1/photo --recent 2 --backup-dir /volume1/mpfix_bk\n",
    )
    p.add_argument("--version", action="version", version="mpfix " + __version__)
    sub = p.add_subparsers(dest="cmd", metavar="<command>")

    s = sub.add_parser("apply", help="patch files (single file / one directory / --tree)")
    s.add_argument("dir", metavar="PATH")
    _range_opts(s)
    s.add_argument("--backup-dir", metavar="DIR",
                   help="backup root; without it nothing is backed up "
                        "(a failed write still rolls back)")
    s.add_argument("--skip", metavar="REL,REL", help="relative directories to skip, comma separated")
    s.add_argument("--make-allow", metavar="MAKE,MAKE",
                   help="extra gate: allowed EXIF Make values")
    s.set_defaults(func=cmd_apply)

    s = sub.add_parser("verify", help="report status: OK / STATIC / MISSING / PENDING / BAD")
    s.add_argument("dir", metavar="PATH")
    _range_opts(s)
    s.add_argument("--offline", action="store_true",
                   help="offline check: the file only, no Synology and no @eaDir")
    s.add_argument("--summary", action="store_true",
                   help="per-directory summary (lightweight, no byte-level comparison)")
    s.add_argument("--skip", metavar="REL,REL",
                   help="relative directories to skip, comma separated")
    s.set_defaults(func=cmd_verify)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args) or 0
