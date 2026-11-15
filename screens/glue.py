#!/usr/bin/python3

import os
import subprocess
import sys

from PIL import Image

import frames


def rate_for(pictures):
    if len(pictures) < 2:
        return 1.0
    seconds = pictures[-1][1]["time"] - pictures[0][1]["time"]
    if seconds <= 0:
        return 0.5
    return (len(pictures) - 1) / seconds


def render(canvas, path, frame):
    for piece, filename in zip(frame["pieces"], frame["files"]):
        with Image.open(os.path.join(path, filename)) as source:
            picture = source.convert("RGB")
        col, row = piece[:2]
        if col < 0:
            canvas = picture
        else:
            tile = frame["tile"]
            canvas.paste(picture, (col * tile, row * tile))
    return canvas


def build(folder, target):
    pictures = frames.read_frames(folder)
    if not pictures:
        return 1
    width, height = pictures[0][1]["size"]
    size = (width, height)
    canvas = Image.new("RGB", size, "white")
    command = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "%dx%d" % size,
        "-framerate", "%.6f" % rate_for(pictures), "-i", "pipe:0",
        "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36",
        "-deadline", "realtime", "-cpu-used", "6", "-threads", "2",
        "-pix_fmt", "yuv420p", target,
    ]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
        try:
            for path, frame in pictures:
                canvas = render(canvas, path, frame)
                encoder.stdin.write(canvas.resize(size).tobytes() if canvas.size != size else canvas.tobytes())
        except (OSError, ValueError, KeyError) as error:
            print(error, file=sys.stderr)
            encoder.kill()
            return 1
        encoder.stdin.close()
        return encoder.wait()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(2)
    sys.exit(build(sys.argv[1], sys.argv[2]))
