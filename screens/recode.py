#!/usr/bin/python3

import json
import os
import shutil
import subprocess
import sys
import tempfile
import zlib

import frames

TILE = int(os.environ.get("DE_SCREEN_TILE", "16"))
QUALITY = os.environ.get("DE_SCREEN_QUALITY", "50")
KEY_SHARE = int(os.environ.get("DE_SCREEN_KEYFRAME_SHARE", "40"))


def raw_bytes(picture, width, height, target):
    subprocess.check_call(
        ["magick", picture, "-resize", "%dx%d!" % (width, height),
         "-depth", "8", "rgb:" + target],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return open(target, "rb").read()


def hashes(data, width, cols, rows):
    out = []
    stride = width * 3
    width_bytes = TILE * 3
    for row in range(rows):
        for col in range(cols):
            value = 0
            left = col * width_bytes
            for y in range(row * TILE, row * TILE + TILE):
                start = y * stride + left
                value = zlib.crc32(data[start:start + width_bytes], value)
            out.append(value)
    return out


def find_runs(changed, cols, rows):
    marks = set(changed)
    out = []
    for row in range(rows):
        col = 0
        while col < cols:
            if row * cols + col in marks:
                start = col
                while col < cols and row * cols + col in marks:
                    col += 1
                out.append((start, row, col - start))
            else:
                col += 1
    return out


def crop(source, width, height, run, target):
    col, row, run_width = run
    subprocess.check_call(
        ["magick", "-size", "%dx%d" % (width, height), "-depth", "8", "rgb:" + source,
         "-crop", "%dx%d+%d+%d" % (run_width * TILE, TILE, col * TILE, row * TILE), "+repage",
         "-quality", QUALITY, "webp:" + target],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def whole(picture, width, height, target):
    subprocess.check_call(
        ["magick", picture, "-resize", "%dx%d!" % (width, height),
         "-quality", QUALITY, "webp:" + target],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def rewrite(folder, target, width, height, step):
    pictures = frames.rebuild(folder, os.path.join(target, "work"), step)
    if not pictures:
        return 0

    cols = width // TILE
    rows = height // TILE
    total = cols * rows
    scratch = os.path.join(target, "scratch.rgb")

    previous = None
    written = 0

    for position, (_, stamp, picture) in enumerate(pictures):
        data = raw_bytes(picture, width, height, scratch)
        current = hashes(data, width, cols, rows)

        if previous is None:
            changed = list(range(total))
        else:
            changed = [i for i in range(total) if previous[i] != current[i]]

        keyframe = previous is None or len(changed) * 100 >= total * KEY_SHARE
        if not changed:
            previous = current
            continue

        path = os.path.join(target, "%010d" % position)
        os.makedirs(path, exist_ok=True)

        names = []
        pieces = []
        if keyframe:
            name = "full.webp"
            whole(picture, width, height, os.path.join(path, name))
            names.append(name)
            pieces.append([-1, 0, 0])
        else:
            for run in find_runs(changed, cols, rows):
                name = "r%d_%d_%d.webp" % run
                crop(scratch, width, height, run, os.path.join(path, name))
                names.append(name)
                pieces.append([run[0], run[1], run[2]])

        meta = {
            "frame": position,
            "time": stamp,
            "size": [width, height],
            "grid": [cols, rows],
            "tile": TILE,
            "keyframe": keyframe,
            "pieces": pieces,
            "files": names,
        }
        out = open(os.path.join(path, "meta.json"), "w")
        out.write(json.dumps(meta))
        out.close()

        for name in names:
            written += os.path.getsize(os.path.join(path, name))

        previous = current

    shutil.rmtree(os.path.join(target, "work"), ignore_errors=True)
    if os.path.exists(scratch):
        os.remove(scratch)
    return written


def carry_record(folder, target, width, height, step, written):
    path = os.path.join(folder, "record.json")
    if not os.path.exists(path):
        return
    try:
        record = json.loads(open(path).read())
    except ValueError:
        return

    record["width"] = width
    record["height"] = height
    record["step"] = step * int(record.get("step", 2))
    record["bytes"] = written

    out = open(os.path.join(target, "record.json"), "w")
    out.write(json.dumps(record))
    out.close()


def main():
    if len(sys.argv) < 5:
        sys.stderr.write("usage: recode.py <папка> <ширина> <высота> <шаг>\n")
        return 1

    folder = sys.argv[1]
    width = int(sys.argv[2])
    height = int(sys.argv[3])
    step = int(sys.argv[4])

    if not os.path.isdir(folder):
        return 1

    parent = os.path.dirname(os.path.abspath(folder))
    target = tempfile.mkdtemp(prefix="de-recode-", dir=parent)
    try:
        written = rewrite(folder, target, width, height, step)
        if not written:
            shutil.rmtree(target, ignore_errors=True)
            return 1
        carry_record(folder, target, width, height, step, written)
        backup = folder + ".old"
        shutil.rmtree(backup, ignore_errors=True)
        os.rename(folder, backup)
        os.rename(target, folder)
        shutil.rmtree(backup, ignore_errors=True)
        return 0
    except (subprocess.CalledProcessError, OSError):
        shutil.rmtree(target, ignore_errors=True)
        return 1


sys.exit(main())
