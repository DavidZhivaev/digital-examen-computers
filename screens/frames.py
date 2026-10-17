#!/usr/bin/python3

import json
import os
import subprocess


def read_frames(folder):
    frames = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        meta_path = os.path.join(path, "meta.json")
        if not os.path.isdir(path) or not os.path.exists(meta_path):
            continue
        try:
            meta = json.loads(open(meta_path).read())
        except ValueError:
            continue
        frames.append((path, meta))
    frames.sort(key=lambda item: item[1].get("frame", 0))
    return frames


def blank(path, width, height):
    subprocess.check_call(
        ["magick", "-size", "%dx%d" % (width, height), "canvas:white", path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def apply_frame(canvas, path, meta):
    tile = meta["tile"]
    for position, piece in enumerate(meta["pieces"]):
        name = meta["files"][position]
        blob = os.path.join(path, name)
        if not os.path.exists(blob):
            continue
        if piece[0] < 0:
            subprocess.check_call(
                ["magick", blob, canvas],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            continue
        subprocess.check_call(
            ["magick", canvas, blob,
             "-geometry", "+%d+%d" % (piece[0] * tile, piece[1] * tile),
             "-composite", canvas],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )


def rebuild(folder, work_dir, step=1):
    """Собрать кадры записи обратно в целые картинки.

    Возвращает список (номер, время, путь к png). Полосы накладываются одна за
    другой на одно полотно, поэтому проходить надо строго по порядку: кадр
    номер N без предшествующих ему не восстанавливается.
    """
    frames = read_frames(folder)
    if not frames:
        return []

    os.makedirs(work_dir, exist_ok=True)
    canvas = os.path.join(work_dir, "canvas.png")
    size = frames[0][1]["size"]
    blank(canvas, size[0], size[1])

    out = []
    kept = 0
    for position, (path, meta) in enumerate(frames):
        apply_frame(canvas, path, meta)
        if position % step:
            continue
        target = os.path.join(work_dir, "f%06d.png" % kept)
        subprocess.check_call(
            ["magick", canvas, target],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        out.append((meta.get("frame", position), meta.get("time", 0), target))
        kept += 1

    return out
