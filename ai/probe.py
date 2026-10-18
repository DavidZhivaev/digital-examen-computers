#!/usr/bin/python3

"""Посчитать признаки ИИ на живой странице.

Нужно затем, что Яндекс с рабочей машины отдаёт капчу — мы ходим через
админский VPN, — и проверить его можно только со школьного компьютера.
Запускать там:

    python3 probe.py https://ya.ru/search/?text=что+такое+фотосинтез

Печатает размер страницы, найденные признаки с весами и что сказал бы
классификатор. Ничего никуда не отправляет.
"""

import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import markers

AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
COUNT_ALSO = ("Нейро", "Алиса", "YandexGPT", "neuro", "AI Overview", "AI Mode")


def fetch(url):
    request = urllib.request.Request(url)
    request.add_header("User-Agent", AGENT)
    request.add_header("Accept-Language", "ru-RU,ru;q=0.9")
    answer = urllib.request.urlopen(request, timeout=30)
    return answer.read().decode("utf-8", "replace")


def main():
    if len(sys.argv) < 2:
        sys.stdout.write(__doc__)
        return 1

    url = sys.argv[1]
    host = url.split("://", 1)[-1].split("/", 1)[0]

    try:
        html = fetch(url)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as error:
        sys.stdout.write("страница не открылась: %s\n" % error)
        return 1

    sys.stdout.write("узел   : %s\n" % host)
    sys.stdout.write("размер : %d байт\n" % len(html.encode("utf-8")))

    if "captcha" in html.lower() or "Подтвердите, что запросы" in html:
        sys.stdout.write("\nЭТО КАПЧА, а не страница. Пробуйте со школьного компьютера.\n")
        return 1

    sys.stdout.write("\nпростым счётом:\n")
    for word in COUNT_ALSO:
        count = html.count(word)
        if count:
            sys.stdout.write("  %-14s %d\n" % (word, count))

    total, found, quote = markers.score(host, url, "", html)
    sys.stdout.write("\nпризнаками классификатора: сумма %.1f\n" % total)
    for kind, word, weight in found:
        sys.stdout.write("  %-6s %-20s %.1f\n" % (kind, word, weight))
    if quote:
        sys.stdout.write("\nцитата: %s\n" % quote[:200])

    sys.stdout.write("\nпорог «точно ИИ» — 6. Сумма %.1f, значит %s\n"
                     % (total, "закрыли бы без модели" if total >= 6 else "решала бы модель"))
    return 0


sys.exit(main())
