#!/usr/bin/python3

import json
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


def encode(target, size, rate, pictures):
    command = [
        "ffmpeg", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "rgb24", "-video_size", "%dx%d" % size,
        "-framerate", "%.6f" % rate, "-i", "pipe:0",
        "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "36",
        "-deadline", "realtime", "-cpu-used", "6", "-threads", "2",
        "-pix_fmt", "yuv420p", target,
    ]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
        try:
            for canvas in pictures:
                encoder.stdin.write(canvas.resize(size).tobytes() if canvas.size != size else canvas.tobytes())
        except (OSError, ValueError, KeyError) as error:
            print(error, file=sys.stderr)
            encoder.kill()
            return 1
        encoder.stdin.close()
        return encoder.wait()


def record_pictures(pictures, size):
    canvas = Image.new("RGB", size, "white")
    for path, frame in pictures:
        canvas = render(canvas, path, frame)
        yield canvas


def build(folder, target):
    pictures = frames.read_frames(folder)
    if not pictures:
        return 1
    size = tuple(pictures[0][1]["size"])
    return encode(target, size, rate_for(pictures), record_pictures(pictures, size))


def lesson_frames(root, computer, start, end):
    timelines = []
    for day in sorted(os.listdir(root)):
        base = os.path.join(root, day, computer)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            folder = os.path.join(base, name)
            try:
                with open(os.path.join(folder, "record.json")) as stream:
                    record = json.load(stream)
                if record["started"] >= end or record["last"] < start - 20:
                    continue
                pictures = [(path, frame) for path, frame in frames.read_frames(folder) if frame["time"] < end]
            except (OSError, ValueError, KeyError):
                continue
            if not pictures or pictures[-1][1]["time"] < start - 20:
                continue
            before = 0
            for index, (_, frame) in enumerate(pictures):
                if frame["time"] >= start:
                    break
                if frame["keyframe"]:
                    before = index
            timelines.extend((frame["time"], folder, path, frame) for path, frame in pictures[before:])
    return sorted(timelines, key=lambda item: (item[0], item[3]["frame"]))


def lesson_pictures(timeline, start, end, size):
    blank = Image.new("RGB", size, "black")
    canvases = {}
    index = 0
    last_time = 0
    last_folder = None
    for second in range(start, end, 2):
        while index < len(timeline) and timeline[index][0] <= second:
            last_time, last_folder, path, frame = timeline[index]
            if last_folder not in canvases:
                canvases[last_folder] = Image.new("RGB", tuple(frame["size"]), "black")
            canvases[last_folder] = render(canvases[last_folder], path, frame)
            index += 1
        yield canvases[last_folder] if second - last_time <= 20 else blank


def build_lesson(root, computer, start, target):
    end = start + 2400
    timeline = lesson_frames(root, computer, start, end)
    if not timeline:
        return 4
    size = (1024, 576)
    return encode(target, size, 0.5, lesson_pictures(timeline, start, end, size))


if __name__ == "__main__":
    if len(sys.argv) == 6 and sys.argv[1] == "--lesson":
        sys.exit(build_lesson(sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]))
    if len(sys.argv) == 3:
        sys.exit(build(sys.argv[1], sys.argv[2]))
    sys.exit(2)
