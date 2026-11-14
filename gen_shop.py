#!/usr/bin/env python3
"""Собирает файлы для API-тега Lolz из публичной страницы https://mix-smm.ru/services.

Ключей и паролей не нужно: страница открыта для всех.
Запуск:        python3 gen_shop.py
Проверка:      python3 gen_shop.py --check        (что нашлось по выбранным ID)
Все услуги:    python3 gen_shop.py --list         (ID, цена, название — чтобы выбрать новые)
Из файла:      python3 gen_shop.py --file page.html [--check|--list]

Если страница не открылась, разметка изменилась или нашлось меньше 80% выбранных услуг —
файлы в docs/ НЕ перезаписываются (в теме остаётся прошлая версия), скрипт завершается с ошибкой.
"""
import json, os, re, shutil, sys, urllib.request, urllib.parse
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "docs")
ALLOWED_HOST = "mix-smm.ru"
MAX_BYTES = 12 * 1024 * 1024
MIN_FOUND = 0.8
WARN_CHARS = 6000
PRICE_RE = re.compile(r"^\d[\d\s\u00a0\u202f]*,\d{2}\s*₽")
NUM_RE = re.compile(r"^\d[\d\s\u00a0\u202f]*$")
EMOJI_RE = re.compile("[\U0001F000-\U0001FFFF\u2190-\u21FF\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u200B\u2060]")
SMALLCAPS_RE = re.compile("[\u0250-\u02FF\u1D00-\u1DBF\u24B6-\u24E9\U0001F150-\U0001F189]")


def to_num(s):
    return float(re.sub(r"[^\d,]", "", s).replace(",", "."))


def to_int(s):
    return int(re.sub(r"\D", "", s))


SC = dict(zip("ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘʀᴛᴜᴠᴡʏᴢ", "ABCDEFGHIJKLMNOPRTUVWYZ"))
TAG_RE = re.compile("^\\s*(?:тт|[a-z]|[\u0250-\u02FF\u1D00-\u1DBF]{1,4})\\s+")


def clean_name(raw):
    s = TAG_RE.sub("", raw)
    s = s.replace("⟮", "(").replace("⟯", ")").replace("♻", "")
    s = "".join(SC.get(c, c) for c in s)
    s = EMOJI_RE.sub("", s)
    s = SMALLCAPS_RE.sub("", s)
    s = re.sub(r"\bR\s+(\d+)", r"R\1", s)
    s = re.sub(r"\(\s*\)", "", s)
    s = re.sub(r"\(\s+", "(", s)
    s = re.sub(r"\s+\)", ")", s)
    s = re.sub(r"[\[\]\x00-\x1f]", "", s)
    return re.sub(r"\s+", " ", s).strip()


class Rows(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self._cells, self._buf, self._skip, self.text = [], None, None, 0, []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "tr":
            self._cells = []
        elif tag in ("td", "th") and self._cells is not None:
            self._buf = []

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("td", "th") and self._cells is not None and self._buf is not None:
            self._cells.append(re.sub(r"\s+", " ", " ".join(self._buf)).strip())
            self._buf = None
        elif tag == "tr" and self._cells is not None:
            self.rows.append(self._cells)
            self._cells = None

    def handle_data(self, data):
        if self._skip:
            return
        self.text.append(data)
        if self._buf is not None:
            self._buf.append(data)


def parse_table(rows):
    out = {}
    for cells in rows:
        cells = [c for c in cells if c]
        if len(cells) < 3 or not re.fullmatch(r"\d{1,6}", cells[0]):
            continue
        pi = next((i for i, c in enumerate(cells) if PRICE_RE.match(c)), None)
        if pi is None or pi < 2:
            continue
        nums = [c for c in cells[pi + 1:] if NUM_RE.match(c)]
        out[cells[0]] = {
            "name": " ".join(cells[1:pi]),
            "rate": to_num(cells[pi]),
            "min": to_int(nums[0]) if len(nums) > 0 else None,
            "max": to_int(nums[1]) if len(nums) > 1 else None,
        }
    return out


def parse_flat(text, ids):
    flat = re.sub(r"\s+", " ", " ".join(text))
    out = {}
    for i in ids:
        m = re.search(r"(?<![\d,.])" + re.escape(str(i)) + r" (?P<n>[^₽]{3,220}?) (?P<p>\d[\d \u00a0]*,\d{2}) ?₽", flat)
        if m:
            out[str(i)] = {"name": m.group("n"), "rate": to_num(m.group("p")), "min": None, "max": None}
    return out


class SameHost(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        u = urllib.parse.urlparse(newurl)
        if u.scheme != "https" or u.hostname != ALLOWED_HOST:
            raise urllib.error.URLError("редирект на чужой адрес запрещён: " + newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url):
    u = urllib.parse.urlparse(url)
    if u.scheme != "https" or u.hostname != ALLOWED_HOST:
        sys.exit("разрешён только https://" + ALLOWED_HOST)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ShopFeedBot/1.0)", "Accept-Language": "ru"})
    with urllib.request.build_opener(SameHost).open(req, timeout=30) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        sys.exit("страница слишком большая")
    return data.decode("utf-8", errors="replace")


def money(v):
    s = f"{v:,.2f}".replace(",", " ").replace(".", ",")
    return (s[:-3] if s.endswith(",00") else s) + " ₽"


def num(v):
    return f"{v:,}".replace(",", " ")


def build(group, svc):
    lines, found, total = [], 0, 0
    for sec in group["sections"]:
        rows = []
        for it in sec["items"]:
            total += 1
            s = svc.get(str(it["id"]))
            if not s:
                print(f"нет на странице: {it['id']}", file=sys.stderr)
                continue
            found += 1
            name = it.get("label") or clean_name(s["name"]) or f"Услуга {it['id']}"
            if it.get("per_piece"):
                rows.append(f"• {name} — {money(s['rate'] / 1000)} за шт.")
            else:
                lim = f" (от {num(s['min'])} до {num(s['max'])})" if s["min"] and s["max"] else ""
                rows.append(f"• {name} — {money(s['rate'])} за 1000{lim}")
        if rows:
            lines += [f"★ {sec['title']}"] + rows + [""]
    stamp = datetime.now(timezone(timedelta(hours=3))).strftime("%d.%m.%Y %H:%M МСК")
    foot = group.get("footer", "")
    return "\n".join(lines).rstrip() + (f"\n\n{foot}" if foot else "") + f"\n\nОбновлено: {stamp}\n", found, total


def main():
    cfg = json.load(open(os.path.join(ROOT, "shop_config.json"), encoding="utf-8"))
    if "--file" in sys.argv:
        html = open(sys.argv[sys.argv.index("--file") + 1], encoding="utf-8").read()
    else:
        try:
            html = fetch(cfg["page_url"])
        except Exception as e:
            sys.exit(f"страница недоступна, файлы не обновлены: {e}")
    p = Rows()
    p.feed(html)
    svc = parse_table(p.rows)
    ids = [it["id"] for g in cfg["groups"] for s in g["sections"] for it in s["items"]]
    if not any(str(i) in svc for i in ids):
        svc.update(parse_flat(p.text, ids))
    if not svc:
        sys.exit("услуги на странице не найдены (изменилась разметка?), файлы не обновлены")

    if "--list" in sys.argv:
        for k, v in svc.items():
            print(k, v["rate"], clean_name(v["name"]), sep="\t")
        return
    built, f_all, t_all = {}, 0, 0
    for g in cfg["groups"]:
        text, f, t = build(g, svc)
        built[g["file"]], f_all, t_all = text, f_all + f, t_all + t
    print(f"найдено {f_all} из {t_all} выбранных услуг, всего на странице {len(svc)}")
    if "--check" in sys.argv:
        print("\n".join(built.values()))
        return
    if t_all == 0 or f_all / t_all < MIN_FOUND:
        sys.exit("нашлось слишком мало услуг, файлы не обновлены")
    os.makedirs(OUT, exist_ok=True)
    for name, text in built.items():
        if len(text) > WARN_CHARS:
            print(f"ВНИМАНИЕ: {name} {len(text)} символов", file=sys.stderr)
        tmp = os.path.join(OUT, name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, os.path.join(OUT, name))
    for r in cfg["rules_files"]:
        shutil.copyfile(os.path.join(ROOT, r), os.path.join(OUT, r))
    print("готово:", ", ".join(list(built) + cfg["rules_files"]))


if __name__ == "__main__":
    main()
