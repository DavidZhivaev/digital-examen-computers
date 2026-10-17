#!/usr/bin/python3

import os
import shutil
import subprocess
import sys
import tempfile

import frames


def build(folder, target):
    work = tempfile.mkdtemp(prefix="de-glue-")
    try:
        pictures = frames.rebuild(folder, work)
        if not pictures:
            return 1

        rate = rate_for(pictures)
        command = [
            "ffmpeg", "-loglevel", "error", "-y",
            "-framerate", "%.4f" % rate,
            "-i", os.path.join(work, "f%06d.png"),
            "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36",
            "-pix_fmt", "yuv420p",
            "-r", "10",
            target,
        ]
        return subprocess.call(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rate_for(pictures):
    """Кадров в секунду так, чтобы запись шла в реальном времени."""
    if len(pictures) < 2:
        return 1.0
    seconds = pictures[-1][1] - pictures[0][1]
    if seconds <= 0:
        return 0.5
    return float(len(pictures) - 1) / seconds


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: glue.py <папка записи> <файл.webm>\n")
        return 1
    return build(sys.argv[1], sys.argv[2])


sys.exit(main())
