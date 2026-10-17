#!/usr/bin/python3

import json
import os
import shutil
import sys


def read_frames(folder):
    frames = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        meta_path = os.path.join(path, "meta.json")
        if not os.path.isdir(path) or not os.path.exists(meta_path):
            continue
        meta = json.loads(open(meta_path).read())
        frames.append((path, meta))
    return frames


def covered(piece):
    col, row, width = piece[0], piece[1], piece[2]
    return set((col + step, row) for step in range(width))


def merge(group):
    start = 0
    for position in range(len(group) - 1, -1, -1):
        if group[position][1]["keyframe"]:
            start = position
            break

    kept = []
    keyframe = False
    full = None

    for path, meta in reversed(group[start:]):
        if meta["keyframe"]:
            keyframe = True
            full = (path, meta["files"][0])
            kept = []
            continue
        for position in range(len(meta["pieces"]) - 1, -1, -1):
            piece = meta["pieces"][position]
            area = covered(piece)
            taken = set()
            for other, _ in kept:
                taken |= covered(other)
            if area - taken:
                kept.append((piece, (path, meta["files"][position])))

    kept.reverse()

    last = group[-1][1]
    return {
        "keyframe": keyframe,
        "full": full,
        "pieces": kept,
        "time": last["time"],
        "login": last["login"],
        "size": last["size"],
        "grid": last["grid"],
        "tile": last["tile"],
        "frame": last["frame"],
    }


def write_frame(target_dir, number, merged):
    path = os.path.join(target_dir, "%010d" % number)
    os.makedirs(path, exist_ok=True)

    pieces = []
    files = []

    if merged["keyframe"] and merged["full"]:
        source, name = merged["full"]
        shutil.copyfile(os.path.join(source, name), os.path.join(path, "full.webp"))
        pieces.append([-1, 0, 0])
        files.append("full.webp")

    for piece, (source, name) in merged["pieces"]:
        out_name = "r%d_%d_%d.webp" % (piece[0], piece[1], piece[2])
        shutil.copyfile(os.path.join(source, name), os.path.join(path, out_name))
        pieces.append([piece[0], piece[1], piece[2]])
        files.append(out_name)

    meta = {
        "login": merged["login"],
        "frame": number,
        "time": merged["time"],
        "size": merged["size"],
        "grid": merged["grid"],
        "tile": merged["tile"],
        "keyframe": merged["keyframe"],
        "pieces": pieces,
        "files": files,
    }
    out = open(os.path.join(path, "meta.json"), "w")
    out.write(json.dumps(meta))
    out.close()

    return sum(os.path.getsize(os.path.join(path, name)) for name in files)


def folder_size(folder):
    total = 0
    for root, dirs, names in os.walk(folder):
        for name in names:
            if name.endswith(".webp"):
                total += os.path.getsize(os.path.join(root, name))
    return total


def main():
    if len(sys.argv) < 4:
        sys.stderr.write("usage: thin.py <source> <target> <factor>\n")
        return 1

    source = sys.argv[1]
    target = sys.argv[2]
    factor = int(sys.argv[3])

    if factor < 2:
        sys.stderr.write("factor must be 2 or more\n")
        return 1

    frames = read_frames(source)
    if not frames:
        sys.stderr.write("нет кадров\n")
        return 1

    os.makedirs(target, exist_ok=True)

    number = 0
    written = 0
    for start in range(0, len(frames), factor):
        group = frames[start:start + factor]
        written += write_frame(target, number, merge(group))
        number += 1

    before = folder_size(source)
    sys.stdout.write("кадров %d -> %d, байт %d -> %d, сжатие %.2fx\n" % (
        len(frames), number, before, written,
        before / float(written) if written else 0))
    return 0


sys.exit(main())
