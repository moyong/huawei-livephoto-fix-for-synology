"""Show the ftyp layout of one or more jpgs - explains why a file was judged SKIP_BRAND / SKIP_BOX.

Usage:
    probe_brands.py <file> [file...]

Prints every ftyp offset, its brand, the 4 preceding bytes (expected to be the box size) and
whether the brand is printable, plus the offsets of GCamera and the XMP namespace
(-1 means absent).
"""
import re
import sys

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)

for path in sys.argv[1:]:
    try:
        with open(path, "rb") as f:
            d = f.read()
    except OSError as e:
        print("%s  cannot read: %s" % (path, e))
        continue

    print("%-34s size=%-9d ftyp=%d  GCamera=%d  xmp_ns=%d"
          % (path.split("/")[-1].split("\\")[-1], len(d), d.count(b"ftyp"),
             d.find(b"GCamera"), d.find(b"http://ns.adobe.com/xap")))

    shown = 0
    for m in re.finditer(b"ftyp", d):
        o = m.start()
        brand = d[o:o + 12]
        pre = d[o - 4:o] if o >= 4 else b""
        presize = int.from_bytes(pre, "big") if len(pre) == 4 else -1
        printable = all(32 <= c < 127 for c in brand[4:8])
        print("      off=%d brand=%r prefix4B=%r(size=%d) printable=%s"
              % (o, brand, pre, presize, printable))
        shown += 1
        if shown >= 3:
            if d.count(b"ftyp") > 3:
                print("      ... and %d more" % (d.count(b"ftyp") - 3))
            break
