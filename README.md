# huawei-livephoto-fix-for-synology

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](#requirements)
[![Platform: Synology DSM 7](https://img.shields.io/badge/platform-DSM%207-1f425f.svg)](#requirements)

Make **Huawei / HONOR "Live Photo" (Motion Photo)** files show up correctly in **Synology Photos** —
LIVE badge, long-press playback and the extracted video all work afterwards.

> 中文说明见 **[README.zh-CN.md](README.zh-CN.md)**。

It inserts a standard **Google Motion Photo XMP** block into the JPEG — the `GCamera:MotionPhoto` /
`MicroVideo` fields that Synology's metadata parser actually looks at. **The appended MP4 is never
modified, not a single byte.**

```
Before:  [ JPEG .............................. | MP4 (ftyp/moov/mdat) ]   <- Synology sees a plain photo
After :  [ JPEG ][ XMP APP1 ][ ................ | MP4 (ftyp/moov/mdat) ]   <- Synology sees a Live Photo
                  ^ +636 bytes, video untouched
```

---

## Quick start

```sh
git clone https://github.com/<you>/huawei-livephoto-fix-for-synology.git
cd huawei-livephoto-fix-for-synology

python3 mpfix verify ~/Desktop/IMG_001.jpg --offline   # MISSING = needs fixing, STATIC = nothing to do
python3 mpfix apply  ~/Desktop/IMG_001.jpg             # patch it, in place
python3 mpfix verify ~/Desktop/IMG_001.jpg --offline   # OK = ready to upload
```

Then upload that file to Synology Photos — the LIVE badge shows up once DSM indexes it.
No SSH, no NAS access needed for this path. For a whole library in place on the NAS, see
[Usage B](#b-fix-in-place-on-the-nas-bulk).

There are exactly **two commands**: `apply` writes, `verify` never does. That also answers the
usual first question: to dry-run, just use `verify` — there is no `--dry-run` flag to remember.
See [Dry run](#dry-run-trying-before-writing).

## Why your Huawei Live Photos are "dead" in Synology Photos

A Huawei Live Photo is a perfectly valid Motion Photo container: a JPEG with an MP4 appended after the
JPEG's EOI marker. The problem is that **Huawei writes no XMP declaration at all**.

Synology Photos decides "is this a motion photo?" purely from metadata — it reads
`Xmp.GCamera.MotionPhoto` / `Xmp.GCamera.MicroVideo` and never inspects the file structure. No XMP,
no Live Photo: the file is imported as a plain still image, no `SYNOPHOTO_FILM_MOTION_PHOTO.mp4` is
extracted, and the phone app shows no LIVE badge.

The fix is therefore metadata-only, and the file stays structurally identical.

## Features

- **Two commands, no overlap** — `apply` is the only one that writes; `verify` physically cannot
- **Structure-based detection**, not filename- or brand-based — five gates must all pass before a file is touched
- **Never rewrites the video**: only a ~636-byte XMP APP1 segment is inserted; the MP4 tail stays byte-identical
- **In-place write**: keeps the inode, so Synology ACLs, owner, permission bits and mtime are all untouched
- **Verify-after-write with automatic rollback**: size, tail SHA-256, XMP uniqueness and `exiv2` field count are
  re-checked; any mismatch restores the original from memory, even when no backup was requested
- **Backup is optional, never silent**: pass `--backup-dir` to keep originals for later recovery
- **Five-state reporting**: `OK` / `STATIC` / `MISSING` / `PENDING` / `BAD`, so "needs fixing" and
  "Synology has not caught up yet" are no longer confused with each other
- **Incremental runs**: `--recent 2` only touches directories changed in the last two days — built for a cron job
- No third-party dependencies: plain Python 3.8+ and POSIX shell

## Requirements

**To patch photos (either mode)**

- Python **3.8+** — Windows, macOS, Linux, or DSM (which ships one at `/usr/bin/python3`)
- Nothing else. No pip install, no third-party packages.

**Only if you want to patch the library in place on the NAS**

- Synology NAS with **DSM 7.x** + **Synology Photos**
- SSH access to DSM
- `exiv2` from the Synology Photos package, used as an extra cross-check on the result:
  `/var/packages/SynologyPhotos/target/usr/bin/exiv2` (when it is missing, a built-in parser is used
  instead — same verdict, one less independent opinion)

## Installation

```sh
git clone https://github.com/<you>/huawei-livephoto-fix-for-synology.git
cd huawei-livephoto-fix-for-synology

./mpfix --version          # python3 mpfix --version on Windows
```

That is the whole installation. To run it on the NAS as well, copy the directory over:

```sh
scp -r huawei-livephoto-fix-for-synology <nas-user>@<nas-ip>:/tmp/
```

## Repository layout

```
mpfix                        CLI entry point
mpfixlib/
  mp4.py                     top-level box walking, video start, mvhd duration
  xmp.py                     Motion Photo XMP template and detection
  exif.py                    EXIF Make / Model (only for the optional allow-list)
  detect.py                  the five gates (classify reads a file, verdict_of_bytes judges content)
  patch.py                   in-place write + verification + rollback
  verify.py                  five-state judging: offline (file vs itself) and server (vs @eaDir)
  walk.py                    directory traversal, skip rules, incremental (--recent) selection
  cli.py                     argument parsing and output
tests/
  fixtures.py                synthetic JPEG + MP4 samples (a few KB, no real photos)
  test_mpfix.py              68 unit tests, no NAS required
tools/probe/                 diagnostics for a single stubborn file
```

The judging rules live in exactly one place (`detect.py`), so the patcher and the verifier
can never drift apart.

## Usage

Two ways to use it — pick whichever matches how much access you have:

| | Where it runs | What it needs | Good for |
|---|---|---|---|
| **A. Fix locally, then upload** | your own PC | Python 3.8+ | a few photos, no SSH into the NAS |
| **B. Fix in place on the NAS** | the NAS over SSH | shell access to DSM | the whole library, thousands of photos |

Either way the file itself ends up byte-identical: only a ~636-byte XMP block is inserted, the
appended video stays untouched. Synology does not care where the patching happened.

### A. Fix locally, then upload (recommended to try first)

```sh
git clone https://github.com/<you>/huawei-livephoto-fix-for-synology.git
cd huawei-livephoto-fix-for-synology

# 1. what is this file? report only, nothing is written
python3 mpfix verify ~/Desktop/IMG_20260101_120000.jpg --offline

# 2. patch it in place
python3 mpfix apply ~/Desktop/IMG_20260101_120000.jpg

# 3. confirm before uploading — offline, does not need the NAS
python3 mpfix verify ~/Desktop/IMG_20260101_120000.jpg --offline
```

Step 1 prints `MISSING` for a Huawei Live Photo that lacks the declaration, and `STATIC` for a
plain still image. Only `MISSING` is worth patching — that is the dry run, see
[Dry run](#dry-run-trying-before-writing).

A whole folder works the same way — **one level, not recursive**:

```sh
python3 mpfix apply ~/Pictures/Camera --backup-dir ~/mpfix_bk
```

Add `--tree` to go deeper, or `--recent 2` to only touch directories changed in the last two days.

What you should see:

```
  [FIX] IMG_20260101_120000.jpg   10.00MB  HUAWEI  HUAWEI Mate 60 Pro  gap=8  3.000s off=10000000
  ...
  OK   IMG_20260101_120000.jpg  (+636 B)
patched 1 / failed 0
RESULT: ALL_OK
```

Without `--backup-dir` nothing is copied. A **recursive** run says so up front instead of
pretending:

```
!! Recursive write into /volume1/photo with no --backup-dir: nothing will be backed up
```

That is still safe: a failed write is rolled back from the original bytes held in memory, not
from a backup file. A backup only buys you the ability to undo the change *later*, or to recover
if the process is killed mid-write. Recursive runs (`--tree` / `--recent`) print an extra
warning line when no backup directory is given, because those can touch thousands of files.

Then upload the patched file to Synology Photos (web UI, mobile app, or File Station — any way that
lands it inside a folder Photos indexes). Synology re-scans the file, reads the XMP declaration,
extracts the video, and the LIVE badge appears. Usually within seconds; a large batch can take a
while.

`--offline` is the whole point of this mode: it checks the file against itself *(declaration parses,
declared offset equals the real tail length, MP4 structure intact)* instead of against Synology's
extracted video. Without it, `verify` compares against `@eaDir` and reports `PENDING` for a
freshly patched file — meaning "Synology has not extracted it yet", not "broken". See
[States and modes](#states-and-modes) for what each one can and cannot prove.

### B. Fix in place on the NAS (bulk)

```sh
# copy the repo over, then run from there
cd /tmp/huawei-livephoto-fix-for-synology
python3 mpfix --help
```

#### B1. See the scale first (nothing is modified)

```sh
python3 mpfix verify "/volume1/photo/<share>" --tree --offline --summary
```

This prints one row per directory: how many files are `OK`, `STATIC`, `MISSING` (waiting to be
patched), `PENDING` (waiting for Synology) and `BAD`. Nothing is written.

`--summary` does a lightweight pass — it reads only the first 256 KB of each file and checks
whether `@eaDir` has the extracted video. It does **not** compare bytes. Files that already carry
a declaration are therefore very cheap; files still waiting to be patched have to be read in full,
because whether a file is a Motion Photo is decided at the end of the file (`ftyp`), not the start.
On a 100k-photo library expect that first run to take a while; after everything is patched, the
same command is orders of magnitude faster.

This is also the bulk dry run. See [Dry run](#dry-run-trying-before-writing) for the three cases
where the `MISSING` count and what `apply` actually does can differ.

#### B2. Try a few files

```sh
python3 mpfix apply "/volume1/photo/<share>/<phone>/DCIM/Camera/2026/09" --backup-dir /tmp/try_bk
```

Every file in that one directory is classified and printed, so you can check that everything listed
as `[FIX]` really is a Live Photo. Open Synology Photos, confirm the LIVE badge appears, then continue.

#### B3. Run at scale

```sh
python3 mpfix apply "/volume1/photo/<share>" --tree \
    --backup-dir "/volume1/homes/<nas-user>/motionphoto_backup" \
    --skip "DEVICE-A/DCIM/Camera/2026/01,DEVICE-A/DCIM/Camera/2026/02" \
    > /tmp/mpfix_run.log 2>&1 &
```

`--tree` is what makes it recursive; without it only the immediate children of the target are
processed. `--skip` takes a comma-separated list of paths relative to the root, so an interrupted
run can be resumed. Backups mirror the directory tree (`<backup-dir>/2026/09/IMG_0001.jpg`).
Progress prints one line per directory; anything that fails is printed with its full path, and the
run ends with `RESULT: ALL_OK`.

Runs on a schedule — only the directories that changed recently:

```sh
python3 mpfix apply "/volume1/photo/<share>" --recent 2 \
    --backup-dir "/volume1/homes/<nas-user>/motionphoto_backup"
```

`--recent N` still walks the whole tree (it has to, in order to know which directories are new) but
only *processes* directories whose mtime is within N days. Directories changed in the last two
minutes are skipped on purpose: they may still be receiving an upload, and will be picked up next
round.

The window is `[now − N days − 2 min, now − 2 min]`: exactly N days wide, shifted back by that
two-minute guard, so a daily run at a fixed time tiles with the previous one instead of leaving a
gap. Still prefer `--recent 2` over `1` for a scheduled job — the spare day is what covers a missed
run (NAS reboot, DSM update), and re-walking already-patched files only costs about 3 ms each.

#### B4. Verify on the server

```sh
# whole tree — XMP, tail-vs-extracted-video SHA-256, permissions, mtime, size delta
python3 mpfix verify "/volume1/photo/<share>" --tree --offline

# after DSM has indexed: compare against what Synology actually extracted
python3 mpfix verify "/volume1/photo/<share>" --tree
```

On success the script prints `RESULT: ALL_VERIFIED`. If it scans zero files it exits with code 2
instead of silently "passing". The second form is the only one that proves Synology itself accepted
the file — see [States and modes](#states-and-modes).

#### B5. Clean up

```sh
rm -rf "<backup root>"     # only after verification passes
```

## Dry run: trying before writing

There is no `--dry-run` flag, because `apply` means write. To see what *would* happen, run the
read-only command — its `MISSING` count is exactly the set `apply` would touch:

```sh
python3 mpfix verify ~/Pictures/Camera --tree --offline   # MISSING = would be patched
python3 mpfix apply  ~/Pictures/Camera --tree             # now actually do it
```

This is not an estimate. Both commands call the same rule function (`detect.verdict_of_bytes`), so
they cannot disagree. And `verify` has no write call anywhere in its code path — it is not `apply`
with a flag flipped, which means there is no way to lose the "don't write" mode by mistyping a
variable in a shell script.

Measured on a mixed sample of 12 files:

```
verify --tree --offline   →  MISSING 2
apply  --tree             →  Total: patchable 2 / patched 2 / failed 0
```

Three things to keep in mind:

1. **Always add `--offline`.** Without it, `verify` compares against `@eaDir`, which does not exist
   on your PC (or is empty right after an upload), so every file looks like `PENDING` or `BAD`.
2. **`BAD` will not be fixed by `apply`.** Those files already carry a declaration and are
   structurally broken; `apply` skips them as `SKIP_ALREADY`. They need a manual rollback.
3. **`--make-allow` is not mirrored.** `verify` does not take that gate, so when you use a `Make`
   allow-list, the `MISSING` count is an upper bound rather than the exact number.

One caveat on cost: proving `MISSING` means proving there is no `ftyp` anywhere in the file, which
requires reading all of it — and `apply` reads the same bytes. A dry run saves the writes and the
irreversible risk, not the time. For a library with tens of thousands of unpatched photos, skip the
dry run: the rules are strict, writes are idempotent, and a failed write rolls back automatically.

## States and modes

`verify` reports one of five states per file:

| State | Meaning | What to do |
|---|---|---|
| `OK` | declared and usable | nothing |
| `STATIC` | plain still image, or carries some other XMP block — not a Motion Photo | nothing |
| `MISSING` | Huawei Motion Photo without the XMP declaration | `mpfix apply` |
| `PENDING` | declared, but Synology has not extracted the video yet | wait for indexing |
| `BAD` | declared but structurally broken, or the extracted video does not match | restore from backup |

Two of these are easy to get wrong: `MISSING` is *not* "fine" (it is exactly what this tool exists
to fix), and `PENDING` is *not* "broken" (Synology simply has not caught up yet). They used to be
lumped in with `STATIC` and `BAD` respectively.

On top of the states there are two modes, which answer different questions:

| | `--offline` | default |
|---|---|---|
| When | before uploading | after uploading **and** DSM has indexed the file |
| Compares against | only the file itself | Synology's own extraction, `@eaDir/<name>/SYNOPHOTO_FILM_MOTION_PHOTO.mp4` |
| Can tell you | "this file meets the conditions for being recognised" | "Synology recognised it, and the extracted video matches byte for byte" |

Three things that trip people up:

1. **"After uploading" means after indexing.** Right after a copy lands on the NAS, DSM has not
   scanned it yet, `@eaDir` is still empty, and the default mode reports `PENDING` for every
   newly patched file. That is a false alarm, not a broken file — wait a minute or two, or trigger
   a re-index from the app, then run it again.
2. **Route B has no "upload" at all.** Files are patched in place, so what the default mode really
   asks is "did Synology extract the video when it re-scanned". The general phrasing: `--offline`
   checks whether the file is right, the default checks whether Synology agrees with it.
3. **The default mode also compares permission bits, owner and mtime against your backup**, if you
   gave `--backup-dir` — a check that in-place writing did not damage file attributes. It never
   demands a specific mode: `644` and `777+ACL` are both normal on a NAS. It also does not fail
   when it cannot read the `@eaDir` copy (that file is root-owned, `660`); it reports `PENDING`
   instead, because "cannot read" is not "broken".

Rule of thumb: **`--offline` is self-certification, the default mode is third-party proof.**
Before uploading you can only self-certify; the proof exists only after DSM has processed the file.

`--summary` is a third axis: it changes the *output shape* (one row per directory instead of one
line per file) and skips the byte-level comparison for speed. It reports the same five states, but
its `OK` only means "the declaration is complete" — plus "@eaDir has the video" in server mode.
Use it to see the shape of a library, not to certify an individual file.

## Tests

```sh
python3 -m unittest discover -s tests -v
```

68 tests over synthetic samples (a few KB each): every verdict branch, offset invariance, tail
integrity, rollback on verification failure (with and without a backup), mtime and permission
preservation, the offline verifier, all five states, `--tree` / `--recent` ranges, single-file
targets, and the CLI itself. No NAS, no real photos, no network.

## Output

`apply` classifies every file it looks at:

```
  [FIX] IMG_20260101_120001.jpg          10.00MB  HUAWEI  HUAWEI Mate 60 Pro  gap=8  3.000s off=10000000
  [SKIP_NO_FTYP] IMG_20260102_090000.jpg  HUAWEI  no ftyp anywhere - plain still image
  [SKIP_ALREADY] IMG_20260103_101010.jpg  HUAWEI  already declares a GCamera Motion Photo
```

| Verdict | Meaning |
|---|---|
| `FIX` | Live Photo without XMP — will be patched |
| `SKIP_ALREADY` | already a proper Motion Photo — untouched |
| `SKIP_NO_FTYP` | plain still image — untouched |
| `SKIP_HAS_XMP` | carries some other XMP block — untouched, needs manual review |
| `SKIP_BRAND` / `SKIP_BOX` / `SKIP_MP4` | MP4 tail is not a self-consistent `ftyp/moov/mdat` chain — untouched |
| `SKIP_MAKE` | only with `--make-allow`: EXIF `Make` not in the allow-list |

`verify` reports the five states described in [States and modes](#states-and-modes), either per
file or one row per directory with `--summary`.

## Safety model

1. **Five gates + two vetoes** — a file is modified only if it is structurally an un-declared Motion Photo.
2. **Additive only** — no existing byte is changed or removed; the video tail's SHA-256 must stay identical.
3. **In-place write** (`r+b` + `truncate`) keeps the inode: Synology ACLs (some directories are
   `777` + `user:PhotoStation ACE`, others plain `644` — both normal), owner and permission bits
   survive either way. A `cp` overwrite would create a new inode and silently downgrade them.
4. **mtime preserved** — otherwise Synology Photos or the phone backup may treat the file as new and re-upload it.
5. **Verify, then roll back** — after writing, `size`, tail SHA-256, XMP uniqueness, the field count
   (= 7, via `exiv2` when present, via the built-in parser otherwise) and the permission bits are
   re-checked; any mismatch restores the original bytes immediately. The rollback reads from memory,
   so it works identically whether or not you asked for a backup.
6. **Nothing is written by accident at the wrong scale** — `apply` is the only command that writes,
   and recursion requires an explicit `--tree` or `--recent`. Without either flag only the target's
   immediate files are touched. Recursive runs without `--backup-dir` print a warning before starting.
7. **Same bytes either way** — patching on a PC and patching on the NAS go through the same code and
   produce byte-identical output, so nothing depends on where you ran it.

## Detection rules in one table

A file is patched only when **all** of these hold:

| # | Gate |
|---|---|
| 1 | extension `.jpg` / `.jpeg` |
| 2 | starts with `FF D8` (JPEG SOI) |
| 3 | contains `ftypmp42` / `ftypisom` / `ftypiso6` at offset > 4 |
| 4 | at that offset − 4 there is a self-consistent MP4 box (`size` plausible, `type == "ftyp"`) |
| 5 | from there to EOF the top-level boxes parse as `ftyp` + `moov` + `mdat`, and `mvhd` duration is 0.2–60 s |

Vetoes: already declared (`GCamera:MotionPhoto` / `MicroVideo`), or any other XMP packet present.

Detection is **structural on purpose**: brand names live only in EXIF, and matching on `Make` produced
false positives during testing. `--make-allow HUAWEI,HONOR` is available as an extra, off-by-default guard.

## Limitations

- Metadata only. It does not write Synology's internal database; the server generates the
  `MotionPhotoAdditional` entry on its next scan.
- Huawei / HONOR is the primary target. Samsung, Xiaomi and others with the same container layout should
  work but are **untested** — run `verify --offline` on the folder and patch a small batch first.
- iPhone Live Photos are a different format (`.HEIC` + separate `.MOV`) and are not supported.
- `@eaDir` thumbnails are ignored by design.
- The `mvhd` duration gate is 0.2–60 s; extremely short or long clips are skipped for manual review.
- Whether the LIVE badge appears after an upload is DSM's business: the file has to be scanned and
  indexed by Synology Photos. If it does not show up within a minute or two, trigger a re-index from
  the app or DSM rather than patching again.

## License

[MIT](LICENSE)
