#!/usr/bin/python3

import json
import os
import sys

import model

DATA_FILE = os.environ.get("DE_AI_DATA", "/models/labelled.jsonl")


def read_samples(path):
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("label") not in (0, 1, True, False):
            continue
        row["label"] = int(bool(row["label"]))
        out.append(row)
    return out


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DATA_FILE
    if not os.path.exists(path):
        sys.stdout.write("нет выборки: %s\n" % path)
        return 1

    samples = read_samples(path)
    if len(samples) < 40:
        sys.stdout.write("в выборке %d строк, для обучения нужно хотя бы 40\n" % len(samples))
        return 1

    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_score

    rows = model.build_rows(samples)
    labels = [item["label"] for item in samples]

    machine = LogisticRegression(max_iter=2000, class_weight="balanced")
    scores = cross_val_score(machine, rows, labels, cv=5, scoring="f1")
    machine.fit(rows, labels)

    info = {
        "samples": len(samples),
        "positive": sum(labels),
        "f1_folds": [round(float(item), 4) for item in scores],
        "f1_mean": round(float(sum(scores) / len(scores)), 4),
        "embed": model.EMBED_NAME,
    }
    model.save(machine, info)

    sys.stdout.write("обучено на %d страницах, F1 по пяти частям %.3f\n"
                     % (len(samples), info["f1_mean"]))
    return 0


sys.exit(main())
