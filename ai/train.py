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
    from sklearn.model_selection import cross_val_predict, cross_val_score

    rows = model.build_rows(samples)
    labels = [item["label"] for item in samples]

    machine = LogisticRegression(max_iter=2000, class_weight="balanced")
    scores = cross_val_score(machine, rows, labels, cv=5, scoring="f1")
    machine.fit(rows, labels)

    best, table = pick_threshold(machine, rows, labels)

    info = {
        "samples": len(samples),
        "positive": sum(labels),
        "f1_folds": [round(float(item), 4) for item in scores],
        "f1_mean": round(float(sum(scores) / len(scores)), 4),
        "threshold": best,
        "embed": model.EMBED_NAME,
        "synthetic": sum(1 for item in samples if "синтетика" in str(item.get("by", ""))),
    }
    model.save(machine, info)

    sys.stdout.write("обучено на %d страницах, F1 по пяти частям %.3f\n"
                     % (len(samples), info["f1_mean"]))

    if info["synthetic"]:
        sys.stdout.write(
            "ВНИМАНИЕ: из них придуманных %d. Пока они в выборке, F1 показывает\n"
            "работоспособность, а не качество.\n" % info["synthetic"])

    sys.stdout.write("\nпорог: лучший %.2f\n" % best)
    for value, score, caught, wrong in table:
        sys.stdout.write("  %.2f  F1 %.3f  поймано %d  зря закрыто %d\n"
                         % (value, score, caught, wrong))
    sys.stdout.write("\nпоставить его: DE_AI_THRESHOLD=%.2f\n" % best)
    return 0


def pick_threshold(machine, rows, labels):
    """Какой порог лучше на этой выборке.

    Считаем честно: вероятности берём перекрёстной проверкой, а не на тех
    же строках, на которых учились. Иначе любой порог выглядит прекрасно.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import cross_val_predict

    fresh = LogisticRegression(max_iter=2000, class_weight="balanced")
    chances = cross_val_predict(fresh, rows, labels, cv=5, method="predict_proba")[:, 1]

    table = []
    best = (0.5, -1.0)
    value = 0.3
    while value <= 0.91:
        caught = sum(1 for chance, label in zip(chances, labels) if chance >= value and label)
        wrong = sum(1 for chance, label in zip(chances, labels) if chance >= value and not label)
        missed = sum(1 for chance, label in zip(chances, labels) if chance < value and label)
        score = 0.0
        if caught:
            precision = caught / float(caught + wrong)
            recall = caught / float(caught + missed)
            score = 2 * precision * recall / (precision + recall)
        table.append((value, score, caught, wrong))
        if score > best[1]:
            best = (value, score)
        value += 0.05

    return round(best[0], 2), table


sys.exit(main())
