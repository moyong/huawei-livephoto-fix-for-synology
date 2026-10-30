"""目录遍历与路径规约。

统一四件事，避免各处各写一遍：
  - 命令行给的路径是文件还是目录，怎么展开成待处理文件列表
  - 哪些目录要跳过（@eaDir 缩略图目录、MP_SKIP 指定的已处理目录）
  - 一个目录里哪些文件算候选（.jpg / .jpeg，忽略隐藏文件）
  - 备份目录的镜像路径怎么算

另有 iter_dirs_recent()：只按目录 mtime 挑出"最近有变化的目录"，供增量处理用。

为什么增量判据是目录 mtime 而不是文件 mtime：Synology Photos 上传时会把文件的
mtime 设成拍摄时间（实测某文件 mtime 为 10-25 20:00，实际写入发生在 10-26 00:01），
所以文件 mtime 根本不能用来找"新上传"。而往目录里新建文件一定会刷新目录 mtime。
"""
import os
import time

THUMB_DIR = "@eaDir"
JPEG_EXT = (".jpg", ".jpeg")


def parse_skip(spec):
    """把 "a/b,c/d" 解析成相对路径集合；空值返回空集。"""
    if not spec:
        return set()
    return {s.strip().strip("/") for s in spec.split(",") if s.strip()}


def list_jpgs(dirpath):
    """列出目录内（不递归）的 jpg/jpeg 文件名，已排序。"""
    out = []
    for name in sorted(os.listdir(dirpath)):
        if name.startswith("."):
            continue
        if os.path.splitext(name)[1].lower() not in JPEG_EXT:
            continue
        if os.path.isfile(os.path.join(dirpath, name)):
            out.append(name)
    return out


def is_jpeg_name(name):
    """文件名是否算候选（扩展名 .jpg / .jpeg，忽略大小写）。"""
    return os.path.splitext(name)[1].lower() in JPEG_EXT


def resolve_targets(path):
    """把命令行给的一个路径展开成 (root, [绝对文件路径])。

    接受两种形态，是本地修单张图的关键：
      - 单个 jpg/jpeg 文件 -> (文件所在目录, [该文件])
      - 目录               -> (目录, 目录内所有 jpg，不递归；递归交给 tree)
    路径不存在或不是 jpg 文件时返回 None。
    """
    ap = os.path.abspath(path)
    if os.path.isfile(ap):
        if not is_jpeg_name(ap):
            return None
        return os.path.dirname(ap), [ap]
    if os.path.isdir(ap):
        return ap, [os.path.join(ap, n) for n in list_jpgs(ap)]
    return None


def iter_jpg_dirs(root, skip=()):
    """递归找出所有含 jpg 的目录，跳过 @eaDir 与 skip 里的相对路径。

    产出 (rel, count)：rel 为相对 root 的路径（root 自身为 "."）。
    """
    root = root.rstrip("/")
    skip = set(skip)
    rows = []
    for dirpath, dirnames, _ in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != THUMB_DIR)
        rel = _norm(os.path.relpath(dirpath, root))
        if rel in skip:
            dirnames[:] = []
            continue
        n = len(list_jpgs(dirpath))
        if n:
            rows.append((rel, n))
    rows.sort()
    return rows


def _norm(rel):
    """把相对路径统一成 / 分隔，便于跨平台比较 --skip 与实际路径。"""
    return rel.replace(os.sep, "/")


def iter_dirs_recent(root, since_days=2.0, min_age_s=120.0, skip=(), now=None):
    """递归列出"最近有变化、且含 jpg"的目录。

    返回 [(rel, jpg 数, 目录 mtime)]，按 rel 排序。

    since_days  窗口宽度（天）；<= 0 表示不设下界
    min_age_s   跳过 mtime 太新的目录 —— 正在上传的目录别碰，等下一轮
    skip        相对路径集合，命中则不进入该子树（同 iter_jpg_dirs）
    now         mtime 参考时间，测试用；默认取当前时间

    窗口是 [now-since_days天-min_age_s, now-min_age_s]，注意下界也跟着 min_age_s
    一起平移，所以窗口宽度精确等于 since_days 天。这样每天同一时刻跑一次，
    相邻两轮的窗口首尾相接，既不重也不漏；如果只把上界减去 min_age_s，
    下界不动，窗口就会短掉那 min_age_s，两次运行之间留下一个永久漏掉的缝。

    与 iter_jpg_dirs 的差别只有一个：这里每个目录只 scandir 一次，文件名与子目录
    在一趟里数完，不对每个文件做 stat。十万张照片的库，这一步从 26s 降到 1s 级。
    """
    root = root.rstrip("/") or "/"
    skip = set(skip)
    now = time.time() if now is None else now
    hi = now - min_age_s
    # 下界由上界减去窗口宽度得到，而不是从 now 直接减：窗口宽度才等于 since_days 天
    lo = hi - since_days * 86400.0 if since_days and since_days > 0 else None

    rows = []
    stack = [root]
    while stack:
        dirpath = stack.pop()
        rel = _norm(os.path.relpath(dirpath, root))
        n = 0
        subs = []
        try:
            with os.scandir(dirpath) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        if e.name != THUMB_DIR:
                            subs.append(e.path)
                    elif not e.name.startswith(".") and is_jpeg_name(e.name):
                        n += 1
        except OSError:
            continue

        if n:
            try:
                mt = os.stat(dirpath).st_mtime
            except OSError:
                mt = 0.0
            if mt <= hi and (lo is None or mt >= lo):
                rows.append((rel, n, mt))

        for p in subs:
            if _norm(os.path.relpath(p, root)) in skip:
                continue
            stack.append(p)

    rows.sort()
    return rows


def iter_target_dirs(root, skip=(), recent_days=None):
    """按范围列出待处理的目录，返回 [(rel, jpg 数)]。

    recent_days 为 None -> 全树所有含 jpg 的目录（--tree）
    给数字            -> 只保留目录 mtime 在这么多天内的（--recent）

    两者都是递归；--recent 收窄的是"处理范围"，不是"遍历范围" —— 要判断哪些
    目录是新的，本来就得走完整棵树。
    """
    if recent_days is None:
        return iter_jpg_dirs(root, skip)
    return [(rel, n) for rel, n, _ in
            iter_dirs_recent(root, skip=skip, since_days=recent_days)]


def bak_path(backup_root, rel, name):
    """备份文件的镜像路径：<backup_root>/<rel>/<name>。"""
    if rel in (".", ""):
        return os.path.join(backup_root, name)
    return os.path.join(backup_root, rel, name)


def server_video_path(jpg_path):
    """群晖为动态照片提取的视频路径。"""
    return os.path.join(os.path.dirname(jpg_path), THUMB_DIR,
                        os.path.basename(jpg_path),
                        "SYNOPHOTO_FILM_MOTION_PHOTO.mp4")
