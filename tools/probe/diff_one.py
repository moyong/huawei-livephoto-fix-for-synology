"""Diagnose one file: compare the JPEG's MP4 tail against the video Synology extracted,
and locate the first differing byte.

Usage: diff_one.py <directory> [filename]
"""
import hashlib
import re
import sys

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
D = sys.argv[1].rstrip("/")
b = sys.argv[2] if len(sys.argv) > 2 else "IMG_20260101_120000.jpg"

a = open(D + "/" + b, "rb").read()
v = open(D + "/@eaDir/" + b + "/SYNOPHOTO_FILM_MOTION_PHOTO.mp4", "rb").read()

BRANDS = (b"ftypmp42", b"ftypisom", b"ftypiso6")
occ = []
for br in BRANDS:
    p = a.find(br)
    if p > 4:
        occ.append((p, br.decode("latin1")))
occ.sort()
print("brand hits:", occ[:5])
if not occ:
    print("!! no target brand anywhere in the file")
    sys.exit(1)
i = occ[0][0] - 4
tail = a[i:]
print("jpg total =", len(a))
print("tail len  =", len(tail), " video len =", len(v))

n = min(len(tail), len(v))
d = -1
for k in range(n):
    if tail[k] != v[k]:
        d = k
        break
print("first differing offset =", d)
if d >= 0:
    print("  tail  :", tail[max(0, d - 16):d + 32])
    print("  video :", v[max(0, d - 16):d + 32])
    print("  head tail :", tail[:24].hex())
    print("  head video:", v[:24].hex())

m = re.search(rb"MicroVideoOffset=\x22\x00\x00\x00(\d+)", a[:4096])
print("XMP offset =", m.group(1).decode() if m else "?", " | actual tail len =", len(tail))
print("sha tail  =", hashlib.sha256(tail).hexdigest())
print("sha video =", hashlib.sha256(v).hexdigest())
