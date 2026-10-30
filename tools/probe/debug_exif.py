import struct
import sys

p = sys.argv[1]
d = open(p, "rb").read()
print("file:", p, "  size:", len(d))
print("first 4 bytes:", d[:4].hex())

i = 2
k = 0
while i + 4 <= len(d) and k < 12:
    if d[i] != 0xFF:
        print("  @%d is not 0xFF (got %02x) -> stop" % (i, d[i]))
        break
    marker = d[i + 1]
    if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
        print("  @%d marker=%02X (no length)" % (i, marker))
        i += 2
        k += 1
        continue
    if marker in (0xD9, 0xDA):
        print("  @%d marker=%02X (end)" % (i, marker))
        break
    seglen = struct.unpack(">H", d[i + 2:i + 4])[0]
    tag = ""
    if marker == 0xE1:
        tag = repr(d[i + 4:i + 16])
    print("  @%-8d marker=FF%02X len=%d %s" % (i, marker, seglen, tag))
    if marker == 0xE1 and d[i + 4:i + 10] == b"Exif\x00\x00":
        base = i + 10
        print("     Exif TIFF @%d  byteorder=%r  magic=%d  ifd0off=%d"
              % (base, d[base:base + 2],
                 struct.unpack(">H", d[base + 2:base + 4])[0]
                 if d[base:base + 2] == b"MM" else
                 struct.unpack("<H", d[base + 2:base + 4])[0],
                 struct.unpack(">I", d[base + 4:base + 8])[0]
                 if d[base:base + 2] == b"MM" else
                 struct.unpack("<I", d[base + 4:base + 8])[0]))
        break
    i += 2 + seglen
    k += 1
