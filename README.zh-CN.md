# huawei-livephoto-fix-for-synology

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#环境要求)
[![Platform: Synology DSM 7](https://img.shields.io/badge/platform-DSM%207-1f425f.svg)](#环境要求)

让**华为 / 荣耀「动态照片」（Live Photo / Motion Photo）**在**群晖 Synology Photos** 里恢复正常：
LIVE 徽标出现、长按可播放、服务端自动提取出视频。

> English: **[README.md](README.md)**

做法是把一段标准的 **Google Motion Photo XMP** 写进 JPEG —— 也就是群晖元数据解析器真正会去读的
`GCamera:MotionPhoto` / `MicroVideo` 字段。**尾部的 MP4 一个字节都不动。**

```
处理前:  [ JPEG .............................. | MP4 (ftyp/moov/mdat) ]   <- 群晖当成普通静态照片
处理后:  [ JPEG ][ XMP APP1 ][ ................ | MP4 (ftyp/moov/mdat) ]   <- 群晖识别为动态照片
                  ^ 新增约 636 字节，视频原样
```

---

## 三步上手

```sh
git clone https://github.com/<you>/huawei-livephoto-fix-for-synology.git
cd huawei-livephoto-fix-for-synology

python3 mpfix verify ~/Desktop/IMG_001.jpg --offline   # MISSING = 待修，STATIC = 不用管
python3 mpfix apply  ~/Desktop/IMG_001.jpg             # 修补，原地写入
python3 mpfix verify ~/Desktop/IMG_001.jpg --offline   # OK = 可以上传了
```

然后把这张图上传到 Synology Photos，群晖索引完就出现 LIVE 徽标。
这条路径不需要 SSH、不需要碰 NAS。要在 NAS 上原地修整个图库，见[使用 B](#b-在-nas-上原地修批量)。

只有**两个命令**：`apply` 会写文件，`verify` 永远不写。这也回答了最常问的那个问题——想干跑就
用 `verify`，没有 `--dry-run` 这个参数要记。见[干跑](#干跑先看不写)。

## 为什么华为动态照片在群晖里"不活"

华为拍的动态照片本身是一个完全合法的 Motion Photo 容器：JPEG 结尾（EOI）之后追加一整段 MP4。
问题在于**华为不写任何 XMP 声明**。

群晖判断"这是不是动态照片"只看元数据 —— 读 `Xmp.GCamera.MotionPhoto` / `Xmp.GCamera.MicroVideo`，
**从不检查文件结构**。没有声明 → 当普通静态图入库 → 不生成
`SYNOPHOTO_FILM_MOTION_PHOTO.mp4` → 手机上自然没有 LIVE 徽标。

所以这是一个纯元数据的修复，文件结构保持完全一致。

## 特点

- **两个命令，职责不重叠** —— `apply` 是唯一会写文件的，`verify` 物理上写不了
- **按结构判定，不看文件名也不看品牌** —— 五道闸门全部通过才会动文件
- **绝不重写视频**：只插入约 636 字节的 XMP APP1 段，尾部 MP4 逐字节一致
- **原地写入**：保留 inode，群晖 ACL、属主、权限位、mtime 全部不受影响
- **写后校验 + 自动回退**：体积、尾部 SHA-256、XMP 唯一性、`exiv2` 字段数逐项核对，
  不符立即用内存里的原始字节还原 —— **没有备份也照样能回退**
- **备份可选，但绝不悄悄发生**：加 `--backup-dir` 才落备份，用于事后反悔
- **五状态报告**：`OK` / `STATIC` / `MISSING` / `PENDING` / `BAD`，
  把"待修"和"群晖还没跟上"从"没问题 / 出问题了"里拆出来
- **增量处理**：`--recent 2` 只碰最近两天有变化的目录，为计划任务设计
- 无第三方依赖：Python 3.8+ 即可

## 环境要求

**只需要修补照片（两种模式都一样）**

- Python **3.8+** —— Windows / macOS / Linux / DSM（DSM 自带 `/usr/bin/python3`）
- 没有别的了。不需要 pip install，不需要任何第三方包。

**只有在 NAS 上原地批量修补时才额外需要**

- 群晖 NAS，**DSM 7.x** + **Synology Photos**
- 能 SSH 登录 DSM
- Synology Photos 包内的 `exiv2`，作为额外一道交叉验证：
  `/var/packages/SynologyPhotos/target/usr/bin/exiv2`
  （没有它时改用内置解析器，结论相同，只是少一个独立意见）

## 安装

```sh
git clone https://github.com/<you>/huawei-livephoto-fix-for-synology.git
cd huawei-livephoto-fix-for-synology

./mpfix --version          # Windows 上用 python3 mpfix --version
```

安装就这一步。如果还想在 NAS 上也跑，把目录拷过去即可：

```sh
scp -r huawei-livephoto-fix-for-synology <账号>@<NAS地址>:/tmp/
```

## 代码结构

```
mpfix                        命令行入口
mpfixlib/
  mp4.py                     顶层 box 遍历、视频起点定位、mvhd 时长
  xmp.py                     Motion Photo XMP 模板与识别
  exif.py                    EXIF Make / Model（仅用于可选白名单闸门）
  detect.py                  五道闸门（classify 负责读文件，verdict_of_bytes 负责判内容）
  patch.py                   原地写 + 校验 + 回退
  verify.py                  五状态判定：离线（文件自校验）与服务端（对比 @eaDir）两种
  walk.py                    目录遍历、跳过规则、增量（--recent）筛选
  cli.py                     参数解析与输出
tests/
  fixtures.py                合成 JPEG + MP4 样本（每个几 KB，不含真实照片）
  test_mpfix.py              68 个单元测试，不需要 NAS
tools/probe/                 单个疑难文件的诊断工具
```

判据只存在于 `detect.py` 一处，所以修补端和校验端不可能各说各话。

## 使用

两种用法，按你手上有多少权限选：

| | 在哪跑 | 需要什么 | 适合 |
|---|---|---|---|
| **A. 本机修，改完上传** | 自己的电脑 | Python 3.8+ | 修几张图，不想开 NAS 的 SSH |
| **B. 在 NAS 上原地修** | NAS（SSH） | DSM 的 shell 权限 | 整个图库、上千张 |

两种方式写出来的文件**逐字节相同**：只插入约 636 字节的 XMP 段，尾部视频不动。
群晖并不关心你在哪台机器上补的。

### A. 本机修一张，然后上传（建议先试这个）

```sh
git clone https://github.com/<you>/huawei-livephoto-fix-for-synology.git
cd huawei-livephoto-fix-for-synology

# 1. 这张是什么？只报告，不动文件
python3 mpfix verify ~/Desktop/IMG_20260101_120000.jpg --offline

# 2. 修补：原地写入
python3 mpfix apply ~/Desktop/IMG_20260101_120000.jpg

# 3. 上传前确认：离线校验，不需要 NAS
python3 mpfix verify ~/Desktop/IMG_20260101_120000.jpg --offline
```

第 1 步对缺声明的华为动态照片会打印 `MISSING`，对普通静态照片打印 `STATIC` —— 只有 `MISSING` 值得修。
这一步就是干跑，见[干跑](#干跑先看不写)。

整个目录也一样（**只处理该目录一层，不递归**）：

```sh
python3 mpfix apply ~/Pictures/Camera --backup-dir ~/mpfix_bk
```

要往下递归加 `--tree`；只想碰最近有变化的目录加 `--recent 2`。

正常输出长这样（界面文案是英文的）：

```
  [FIX] IMG_20260101_120000.jpg   10.00MB  HUAWEI  HUAWEI Mate 60 Pro  gap=8  3.000s off=10000000
  ...
  OK   IMG_20260101_120000.jpg  (+636 B)
patched 1 / failed 0
RESULT: ALL_OK
```

不给 `--backup-dir` 时不备份。**递归**跑时程序会先直说一句，而不是假装备了：

```
!! Recursive write into /volume1/photo with no --backup-dir: nothing will be backed up
```

这依然安全：写坏了是从内存里的原始字节回退，不依赖备份文件。备份只买到两件事 ——
**事后反悔**，以及**进程写到一半被强杀**时的兜底。递归跑（`--tree` / `--recent`）且没给
`--backup-dir` 时，还会额外打一行警告，因为那可能一次动几千张。

然后把修好的文件上传到 Synology Photos（网页、手机 App、File Station 都行，只要落在 Photos 会索引的
目录里）。群晖会重新扫描该文件、读到 XMP 声明、提取出视频，LIVE 徽标就出来了 —— 通常几秒；
一次传很多张要等一会儿。

`--offline` 就是这个模式的关键：它拿文件**自己**做校验（声明能否解析、声明偏移是否等于真实尾长、
MP4 结构是否完整），而不是去比对群晖提取出来的视频。不加 `--offline` 时，对刚修好的文件会报
`PENDING` —— 意思是"群晖还没提取"，不是"坏了"。每个状态能证明什么、不能证明什么，见
[状态与模式](#状态与模式)。

### B. 在 NAS 上原地修（批量）

```sh
cd /tmp/huawei-livephoto-fix-for-synology
python3 mpfix --help
```

#### B1. 先看规模（不改任何文件）

```sh
python3 mpfix verify "/volume1/photo/<共享目录>" --tree --offline --summary
```

一行一个目录，给出 `OK` / `STATIC` / `MISSING`（待修）/ `PENDING`（等群晖）/ `BAD` 各多少张。
这一步不写任何文件。

`--summary` 走的是轻量档：只读每个文件的前 256 KB，加上看 `@eaDir` 有没有提取产物，**不做逐字节比对**。
所以已声明的文件很便宜；还等着修的必须读全文 —— 因为"是不是动态照片"这个信息在文件尾（`ftyp`），
不在文件头。十万张的库第一次跑要等一会儿，等全部修完之后同一条命令会快几个量级。

这也是批量干跑。`MISSING` 数量和 `apply` 实际动作之间有三处可能不一致，见[干跑](#干跑先看不写)。

#### B2. 小批量试跑

```sh
python3 mpfix apply "/volume1/photo/<共享目录>/<机型>/DCIM/Camera/2026/09" --backup-dir /tmp/try_bk
```

这一个目录里的每个文件都会给出判定并打印，**先确认 `[FIX]` 列表里没有不该改的文件**（比如截图、
微信图片），再往下走。打开 Synology Photos 确认 LIVE 徽标出现，再继续。

#### B3. 批量执行

```sh
python3 mpfix apply "/volume1/photo/<共享目录>" --tree \
    --backup-dir "/volume1/homes/<账号>/motionphoto_backup" \
    --skip "机型A/DCIM/Camera/2026/01,机型A/DCIM/Camera/2026/02" \
    > /tmp/mpfix_run.log 2>&1 &
```

`--tree` 才是"递归"的开关；不加它只处理目标目录的直接子文件。`--skip` 是相对根目录的路径列表
（逗号分隔），中断后据此续跑。备份按原目录结构镜像（`<备份根>/2026/09/IMG_0001.jpg`）。
每处理完一个目录打一行进度；任何失败都会连完整路径一起打出来，跑完以 `RESULT: ALL_OK` 结束。

挂计划任务时只处理新上传的目录：

```sh
python3 mpfix apply "/volume1/photo/<共享目录>" --recent 2 \
    --backup-dir "/volume1/homes/<账号>/motionphoto_backup"
```

`--recent N` 仍然会遍历整棵树（不遍历就不知道哪些目录是新的），但只**处理** mtime 在 N 天内的目录。
最近两分钟内变过的目录会被故意跳过 —— 那里可能还在写入，留给下一轮。

窗口是 `[现在 − N 天 − 2 分钟, 现在 − 2 分钟]`：宽度精确等于 N 天，整体后移那两分钟，所以每天
固定时刻跑一次，相邻两轮首尾相接，不漏也不重。但计划任务仍建议用 `--recent 2` 而不是 `1` ——
多出来的那一天是冗余，用来扛一次漏跑（NAS 重启、DSM 更新）；重复处理已修好的文件约 3ms/张。

#### B4. 在服务端校验

```sh
# 整树：XMP、声明偏移、尾部 MP4 结构、权限、mtime、体积增量
python3 mpfix verify "/volume1/photo/<共享目录>" --tree --offline

# 等 DSM 索引完之后：与群晖实际提取出的视频逐字节比对
python3 mpfix verify "/volume1/photo/<共享目录>" --tree
```

通过时打印 `RESULT: ALL_VERIFIED`。若一个文件都没扫到，会以退出码 2 结束，而不是假装通过。
第二种才是唯一能证明群晖自己认了这个文件的模式 —— 见[状态与模式](#状态与模式)。

#### B5. 清理

```sh
rm -rf "<备份根目录>"     # 校验通过之后再删
```

## 干跑：先看不写

没有 `--dry-run` 参数，因为 `apply` 就是写的意思。想先看会发生什么，跑只读的那个命令——它报的
`MISSING` 数量就是 `apply` 会动的文件集合：

```sh
python3 mpfix verify ~/Pictures/Camera --tree --offline   # MISSING = 会被修补
python3 mpfix apply  ~/Pictures/Camera --tree             # 确认无误再真跑
```

这不是估算。两个命令调的是同一个判据函数（`detect.verdict_of_bytes`），所以不可能给出不同结论。
而且 `verify` 的代码路径里没有任何写调用——它不是“`apply` 翻了个开关”，因此不存在脚本里变量写错
就把“不写”模式弄丢的可能。

实测对比（12 个混合样本）：

```
verify --tree --offline   →  MISSING 2
apply  --tree             →  Total: patchable 2 / patched 2 / failed 0
```

三个要注意的点：

1. **一定要加 `--offline`。** 不加就是服务端模式，会去比对 `@eaDir`。你自己的电脑上没有它，
   群晖刚传完时它也是空的，于是每个文件都会显示 `PENDING` 或 `BAD`，纯属误报。
2. **`BAD` 不会被 `apply` 修好。** 这类文件已经有声明但结构坏了，`apply` 按 `SKIP_ALREADY` 跳过。
   它们只能人工回滚。
3. **`--make-allow` 两边不对齐。** `verify` 不接受这个闸门，所以用 `Make` 白名单时，`MISSING`
   的数字是上限，不是精确值。

一个成本提醒：判 `MISSING` 要证明“全文任何位置都没有 `ftyp`”，必须读完整文件；`apply` 读的是同
样的字节。所以干跑省的是写入和不可逆的风险，不是时间。库里还有几万张没修的时候，别干跑——判据
严格、写入幂等、写坏会自动回退，直接跑更划算。

## 状态与模式

`verify` 对每个文件给出五种状态之一：

| 状态 | 含义 | 该怎么办 |
|---|---|---|
| `OK` | 已声明且可用 | 什么都不用做 |
| `STATIC` | 普通静态照片，或含其他 XMP 包 —— 不是动态照片 | 什么都不用做 |
| `MISSING` | 华为动态照片，但缺 XMP 声明 | 跑 `mpfix apply` |
| `PENDING` | 已声明，但群晖还没提取出视频 | 等索引 |
| `BAD` | 已声明但结构坏了，或与群晖提取的视频不符 | 从备份还原 |

其中两个最容易看错：`MISSING` **不是**"正常"（它正是这个工具存在的理由），`PENDING` **不是**"坏了"
（只是群晖还没跟上）。这两种以前分别被混进 `STATIC` 和 `BAD` 里。

在状态之上还有两个模式，回答的是两个不同的问题：

| | `--offline` | 默认（不加参数） |
|---|---|---|
| 什么时候用 | 上传**之前** | 上传**之后**，并且 DSM 已经索引完该文件 |
| 拿什么比对 | 只有文件自身 | 群晖自己的提取产物 `@eaDir/<文件名>/SYNOPHOTO_FILM_MOTION_PHOTO.mp4` |
| 能回答 | “这文件具备了被识别的条件” | “群晖已经识别，且提取出的视频逐字节一致” |

三个容易踩的点：

1. **“上传之后”指的是索引完，不是刚拷进去。** 文件刚落到 NAS 上，DSM 还没扫描，`@eaDir` 里
   什么都没有，这时跑默认模式，每个刚修好的文件都是 `PENDING`。**这是误报，不是文件坏了** ——
   等一两分钟，或者在 App 里手动触发一次重新索引，再跑。
2. **B 路线根本没有“上传”这个动作。** 文件是在 NAS 上原地改的，默认模式问的其实是“群晖重新扫描时
   有没有把视频提出来”。通用的说法是：`--offline` 验“文件对不对”，默认模式验“群晖认不认”。
3. **默认模式还会拿备份比权限位、属主、mtime。** 这是在查原地写有没有把文件属性弄坏，
   比的是“修完和修前一样”，**不要求某个固定值** —— `644` 和 `777+ACL` 在 NAS 上都是正常的。
   另外它读不到 `@eaDir` 里的提取物时（那个文件属 root、`660`）报 `PENDING` 而不是 `BAD`，
   因为“读不到”不等于“坏了”。

一句话记法：**`--offline` 是自证，默认模式是第三方证明。** 上传前你只能自证；第三方证明只有等
DSM 处理过这个文件之后才存在。

`--summary` 是第三个维度：它改的是**输出形态**（一行一个目录，而不是一行一个文件），并为了速度跳过
逐字节比对。它报的是同样五种状态，但那里的 `OK` 只表示“XMP 声明完整”—— 服务端模式下再加上
“`@eaDir` 已提取”。它用来摸清一个库的形状，不用来给单个文件背书。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

68 个测试全跑在合成样本上（每个几 KB）：每个判定分支、偏移不变性、尾部完整性、
校验失败自动回退（有备份与无备份各一条）、mtime 与权限保持、离线校验、五种状态、
`--tree` / `--recent` 两种范围、单文件目标，以及 CLI 本身。
不需要 NAS、不需要真实照片、不联网。

## 输出怎么读

`apply` 会给它看过的每个文件一个判定：

```
  [FIX] IMG_20260101_120001.jpg          10.00MB  HUAWEI  HUAWEI Mate 60 Pro  gap=8  3.000s off=10000000
  [SKIP_NO_FTYP] IMG_20260102_090000.jpg  HUAWEI  no ftyp anywhere - plain still image
  [SKIP_ALREADY] IMG_20260103_101010.jpg  HUAWEI  already declares a GCamera Motion Photo
```

| 判定 | 含义 |
|---|---|
| `FIX` | 缺 XMP 的动态照片，将被修补 |
| `SKIP_ALREADY` | 已经是标准动态照片，不动 |
| `SKIP_NO_FTYP` | 纯静态照片，不动 |
| `SKIP_HAS_XMP` | 含其他 XMP 包，不动，需人工判断 |
| `SKIP_BRAND` / `SKIP_BOX` / `SKIP_MP4` | 尾部 MP4 的 `ftyp/moov/mdat` 链不自洽，不动 |
| `SKIP_MAKE` | 仅在 `--make-allow` 时出现：EXIF `Make` 不在白名单 |

`verify` 报的是[状态与模式](#状态与模式)里那五种状态，逐文件一行，或加 `--summary` 一行一个目录。

## 安全设计

1. **五道闸门 + 两条否决** —— 只有结构上确实是"缺声明的动态照片"才会被改。
2. **只加不减** —— 不修改、不删除任何已有字节；尾部视频 SHA-256 必须前后一致。
3. **原地写**（`r+b` + `truncate`）保留 inode：群晖 ACL（有的目录是 `777` + `user:PhotoStation ACE`，
   有的是普通 `644`，两种都正常）、属主、权限位全部存活。用 `cp` 覆盖会新建 inode，
   悄悄把它们改掉。
4. **保留 mtime** —— 否则 Synology Photos 或手机备份可能判为新文件重复上传。
5. **写后校验，不符即回退** —— `size`、尾部 SHA-256、XMP 唯一性、字段数（= 7，有 `exiv2` 时用它，
   没有则用内置解析器）、权限位逐项核对，任一不符立即还原原始字节。回退读的是内存里的数据，
   所以**有没有备份都一样可靠**。
6. **不会在错误的规模上误写** —— `apply` 是唯一会写的命令，递归必须显式给 `--tree` 或 `--recent`；
   两个都不给时只处理目标目录的直接子文件。递归跑又不给 `--backup-dir` 时，开跑前先打警告。
7. **在哪跑结果都一样** —— 本机修和在 NAS 上修走同一份代码，产物逐字节相同，不依赖运行位置。

## 判定规则一览

以下**全部**成立才会修补：

| # | 闸门 |
|---|---|
| 1 | 扩展名 `.jpg` / `.jpeg` |
| 2 | 首两字节 `FF D8`（JPEG SOI） |
| 3 | 文件内含 `ftypmp42` / `ftypisom` / `ftypiso6`，且位置 > 4 |
| 4 | 该位置 −4 处是自洽的 MP4 box（`size` 合理、`type == "ftyp"`） |
| 5 | 到文件尾能解析出 `ftyp` + `moov` + `mdat`，且 `mvhd` 时长在 0.2–60 秒 |

否决项：已声明（`GCamera:MotionPhoto` / `MicroVideo`）、或已含其他 XMP 包。

判定**故意按结构而非品牌**：品牌只写在 EXIF 里，实测按 `Make` 匹配会产生误报。
需要额外保险时可用 `--make-allow HUAWEI,HONOR`（默认关闭）。

## 已知限制

- 只补元数据，不写群晖内部数据库；`MotionPhotoAdditional` 条目由服务端下次扫描时自行生成。
- 主要目标是华为 / 荣耀。三星、小米等相同容器结构的文件理论上可用，但**未实测** ——
  请先对那个目录跑 `verify --offline`，再小批量试。
- iPhone 的 Live Photo 是另一种格式（`.HEIC` + 独立 `.MOV`），不支持。
- `@eaDir` 里的缩略图按设计忽略。
- `mvhd` 时长闸门为 0.2–60 秒，过短/过长的视频会被跳过等人工复核。
- 上传后 LIVE 徽标出不出现，取决于 DSM 有没有扫描并索引该文件；如果一两分钟还没出现，
  在 App 或 DSM 里触发一次重新索引，不要重复修补文件（文件本身已经是对的）。

## 许可

[MIT](LICENSE)
