#!/usr/bin/python3

import base64
import json
import os
import sys

TEMPLATE_HEAD = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Откат экрана</title>
<style>
:root {
  --bg: #f4f5f7;
  --panel: #ffffff;
  --line: #d8dbe0;
  --text: #1b1d21;
  --muted: #6b7280;
  --accent: #2f6fd0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #15171a; --panel: #1e2126; --line: #31353c;
    --text: #e8eaed; --muted: #9aa0a8; --accent: #6aa2f0;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font: 14px/1.45 -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}
.wrap { max-width: 1340px; margin: 0 auto; padding: 16px; }
h1 { font-size: 18px; margin: 0 0 2px; }
.sub { color: var(--muted); font-size: 13px; margin-bottom: 14px; }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }
.screen { padding: 10px; }
canvas { width: 100%; height: auto; display: block; background: #fff; border-radius: 6px; }
.bar { display: flex; gap: 10px; align-items: center; padding: 10px; flex-wrap: wrap; margin-top: 10px; }
button {
  font: inherit; padding: 7px 14px; border-radius: 7px; cursor: pointer;
  border: 1px solid var(--line); background: var(--panel); color: var(--text);
}
button.main { background: var(--accent); border-color: var(--accent); color: #fff; }
button:hover { filter: brightness(1.06); }
input[type=range] { flex: 1 1 260px; min-width: 200px; accent-color: var(--accent); }
.clock { font-variant-numeric: tabular-nums; font-weight: 600; min-width: 86px; }
.meta { display: flex; gap: 18px; flex-wrap: wrap; padding: 10px; color: var(--muted); font-size: 13px; }
.meta b { color: var(--text); font-weight: 600; }
.kind { font-size: 12px; padding: 2px 8px; border-radius: 20px; border: 1px solid var(--line); }
.strip { display: flex; gap: 2px; height: 26px; padding: 10px; align-items: stretch; }
.strip i { flex: 1 1 auto; border-radius: 2px; background: var(--line); cursor: pointer; }
@media (max-width: 760px) { .wrap { padding: 16px; } }
</style>
</head>
<body>
<div class="wrap">
"""

TEMPLATE_TAIL = """
<script>
const W = DATA.width, H = DATA.height, TILE = DATA.tile, COLS = DATA.cols;
const canvas = document.getElementById('screen');
canvas.width = W; canvas.height = H;
const ctx = canvas.getContext('2d');
const images = {};
let current = -1, playing = false, speed = 1, timer = null;

function loadImage(src) {
  if (images[src]) return images[src];
  const img = new Image();
  img.src = src;
  images[src] = img;
  return img;
}

DATA.frames.forEach(f => f.pieces.forEach(p => loadImage(p.d)));

function drawPiece(piece) {
  const img = loadImage(piece.d);
  if (piece.i < 0) {
    ctx.drawImage(img, 0, 0, W, H);
    return;
  }
  const x = (piece.i % COLS) * TILE;
  const y = Math.floor(piece.i / COLS) * TILE;
  ctx.drawImage(img, x, y);
}

function lastKeyBefore(index) {
  for (let i = index; i >= 0; i--) if (DATA.frames[i].k) return i;
  return 0;
}

function show(index) {
  index = Math.max(0, Math.min(DATA.frames.length - 1, index));
  if (index === current) return;
  const start = (index > current && current >= 0) ? current + 1 : lastKeyBefore(index);
  for (let i = start; i <= index; i++) DATA.frames[i].pieces.forEach(drawPiece);
  current = index;
  paint(index);
}

function paint(index) {
  const f = DATA.frames[index];
  document.getElementById('slider').value = index;
  document.getElementById('clock').textContent = f.hm;
  document.getElementById('pos').textContent = (index + 1) + ' / ' + DATA.frames.length;
  document.getElementById('kind').textContent = f.k ? 'ключевой кадр' : (f.n + ' изменённых участков');
  document.getElementById('size').textContent = f.b + ' байт';
  document.querySelectorAll('.strip i').forEach((el, i) => {
    el.style.outline = (i === index) ? '2px solid var(--accent)' : 'none';
  });
}

function step() {
  if (!playing) return;
  if (current >= DATA.frames.length - 1) { stop(); return; }
  show(current + 1);
  const gap = Math.max(60, (DATA.frames[current].gap || 1000) / speed);
  timer = setTimeout(step, gap);
}

function play() { playing = true; document.getElementById('play').textContent = 'Пауза'; step(); }
function stop() { playing = false; clearTimeout(timer); document.getElementById('play').textContent = 'Смотреть'; }

document.getElementById('play').onclick = () => playing ? stop() : play();
document.getElementById('slider').oninput = e => { stop(); show(parseInt(e.target.value, 10)); };
document.getElementById('speed').onclick = () => {
  speed = speed === 1 ? 2 : speed === 2 ? 4 : 1;
  document.getElementById('speed').textContent = 'Скорость ' + speed + '\\u00d7';
};
document.querySelectorAll('.strip i').forEach((el, i) => {
  el.onclick = () => { stop(); show(i); };
});

window.addEventListener('load', () => {
  setTimeout(() => { current = -1; show(0); }, 150);
});
</script>
</body>
</html>
"""


def read_frames(folder):
    frames = []
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        meta_path = os.path.join(path, "meta.json")
        if not os.path.isdir(path) or not os.path.exists(meta_path):
            continue
        meta = json.loads(open(meta_path).read())
        pieces = []
        size = 0
        for position, index in enumerate(meta["pieces"]):
            file_name = meta["files"][position]
            blob = open(os.path.join(path, file_name), "rb").read()
            size += len(blob)
            pieces.append({
                "i": index,
                "d": "data:image/webp;base64," + base64.b64encode(blob).decode(),
            })
        frames.append({
            "t": meta["time"],
            "k": bool(meta["keyframe"]),
            "n": len([i for i in meta["pieces"] if i >= 0]),
            "b": size,
            "pieces": pieces,
            "meta": meta,
        })
    return frames


def main():
    if len(sys.argv) < 3:
        sys.stderr.write("usage: build-playback.py <folder> <out.html> [login]\n")
        return 1

    folder = sys.argv[1]
    target = sys.argv[2]
    login = sys.argv[3] if len(sys.argv) > 3 else os.path.basename(folder.rstrip("/"))

    frames = read_frames(folder)
    if not frames:
        sys.stderr.write("нет кадров\n")
        return 1

    import time as time_module
    first = frames[0]["meta"]

    for position, frame in enumerate(frames):
        frame["hm"] = time_module.strftime("%H:%M:%S", time_module.localtime(frame["t"]))
        if position + 1 < len(frames):
            frame["gap"] = max(200, (frames[position + 1]["t"] - frame["t"]) * 1000)
        else:
            frame["gap"] = 1000

    payload = {
        "width": first["size"][0],
        "height": first["size"][1],
        "tile": first["tile"],
        "cols": first["grid"][0],
        "frames": [
            {"t": f["t"], "hm": f["hm"], "k": f["k"], "n": f["n"], "b": f["b"],
             "gap": f["gap"], "pieces": f["pieces"]}
            for f in frames
        ],
    }

    total = sum(f["b"] for f in frames)
    span = frames[-1]["t"] - frames[0]["t"]
    day = time_module.strftime("%d.%m.%Y", time_module.localtime(frames[0]["t"]))

    head = TEMPLATE_HEAD
    head += "<h1>Откат экрана: %s</h1>\n" % login
    head += "<div class=\"sub\">%s, с %s по %s &middot; %d кадров &middot; %d КБ всего</div>\n" % (
        day, frames[0]["hm"], frames[-1]["hm"], len(frames), total // 1024)
    head += "<div class=\"panel screen\"><canvas id=\"screen\"></canvas></div>\n"
    head += "<div class=\"panel\"><div class=\"bar\">"
    head += "<button id=\"play\" class=\"main\">Смотреть</button>"
    head += "<button id=\"speed\">Скорость 1&times;</button>"
    head += "<span class=\"clock\" id=\"clock\">--:--:--</span>"
    head += "<input type=\"range\" id=\"slider\" min=\"0\" max=\"%d\" value=\"0\">" % (len(frames) - 1)
    head += "</div><div class=\"strip\">"
    for frame in frames:
        head += "<i title=\"%s\"></i>" % frame["hm"]
    head += "</div><div class=\"meta\">"
    head += "<span>Кадр <b id=\"pos\">1</b></span>"
    head += "<span class=\"kind\" id=\"kind\">&mdash;</span>"
    head += "<span>Передано <b id=\"size\">0</b></span>"
    head += "<span>Запись <b>%d с</b></span>" % span
    head += "<span>Разрешение <b>%dx%d</b>, тайл <b>%d</b></span>" % (
        payload["width"], payload["height"], payload["tile"])
    head += "</div></div>\n</div>\n"
    head += "<script>const DATA = %s;</script>\n" % json.dumps(payload)

    out = open(target, "w")
    out.write(head)
    out.write(TEMPLATE_TAIL)
    out.close()

    sys.stdout.write("кадров: %d, байт тайлов: %d, файл: %d КБ\n" % (
        len(frames), total, os.path.getsize(target) // 1024))
    return 0


sys.exit(main())
