"""mpfix —— 华为动态照片 XMP 修补工具库。

模块划分：
    mp4      顶层 box 遍历、视频起点定位、mvhd 时长
    xmp      Motion Photo XMP 构造与识别
    exif     EXIF Make / Model 读取（仅供可选白名单闸门）
    detect   五道闸门判据（classify 读文件，verdict_of_bytes 判内容）
    patch    原地写 + 校验 + 回退
    verify   五种状态判定（OK / STATIC / MISSING / PENDING / BAD）
    walk     目录遍历与路径规约
    cli      命令行入口（apply / verify）
"""

__version__ = "0.5.2"

