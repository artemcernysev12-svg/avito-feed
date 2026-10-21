# -*- coding: utf-8 -*-
"""Наличие товара и комплекта — КОПИЯ логики сайта из build-feed-vk.py (чат «ВК», строки 398–632),
которая сама повторяет app.js (applyStock, chooseKitCompRow, kitCompStockSummary).
Если на сайте поменяют правило наличия — обновить и здесь (скопировать тот же кусок).
Глобальные CAT_BY_ID (весь каталог по ID) и KIT_COMP (составы комплектов) заполняет avito_feed.py.
"""
import functools
import re

CAT_BY_ID, KIT_COMP = {}, {}

# ===== НАЛИЧИЕ — ровно как на сайте (app.js: applyStock, getDisplayAvailability) =====
# Сайт берёт артикул из поля `art` товара (а не из атрибута «Арт поставщика»: у ~20 товаров
# они расходятся), выкидывает из него все пробелы и ищет в stock.json.
ALWAYS_IN_STOCK_IDS = {"7963849000", "7931639186"}   # app.js ALWAYS_IN_STOCK_IDS


def norm_art(v):
    return re.sub(r"[\s ]+", "", str(v or ""))


def stock_by_art(art, stock):
    k = norm_art(art)
    if not k:
        return 0
    try:
        return max(0, int(float(stock.get(k, 0) or 0)))
    except (TypeError, ValueError):
        return 0


def qty_of(it, stock):
    """Остаток товара (как it._stockQty на сайте); 0 — если арта нет или нет остатка."""
    q = stock_by_art(it.get("art"), stock)
    if str(it.get("id")) in ALWAYS_IN_STOCK_IDS:
        q = max(1, q)
    return q


def in_stock(it, stock):
    """True — лежит на складе; False — под заказ (нет арта либо нулевой остаток)."""
    return qty_of(it, stock) > 0


# ----- комплекты: наличие по составу (app.js: chooseKitCompRow, kitCompStockSummary) -----
# Комплект в наличии, только если на складе есть ВСЕ модули активного состава; число
# комплектов — по самому дефицитному артикулу (общий артикул разных строк суммирует
# потребность). В группе вариантов (одинаковый order: кровать 140/160, шкаф с зеркалом/без)
# сайт берёт тот же вариант, что показывает покупателю по умолчанию, — повторяем его выбор.

def _kit_num(v):
    m = re.match(r"\d*\.?\d*", re.sub(r"[^0-9.]", "", str("" if v is None else v).replace(",", ".", 1)))
    try:
        return float(m.group(0)) if m and m.group(0) not in ("", ".") else 0
    except ValueError:
        return 0


def _kit_item(row):
    return CAT_BY_ID.get(str((row or {}).get("module_avito_id") or (row or {}).get("module_item_id") or ""))


def _kit_title(row, item):
    return (item or {}).get("t") or row.get("module_title") or row.get("module_code") or row.get("module_art") or "—"


def _kit_text(row):
    item = _kit_item(row)
    a = (item or {}).get("a") or {}
    parts = [_kit_title(row, item), a.get("Состав комплекта"), a.get("Тип столов"), a.get("Тип шкафов"),
             a.get("Тип модуля"), a.get("Исполнение"), a.get("Особенности"), a.get("Цвет от производителя")]
    return " ".join(str(x) for x in parts if x).lower().replace("ё", "е")


def _kit_price(row):
    item = _kit_item(row)
    p = (item or {}).get("p") or row.get("matched_price") or row.get("price") or 0
    try:
        return float(p)
    except (TypeError, ValueError):
        return 0


def _kit_mirror_label(row, rows):
    txt = _kit_text(row)
    if re.search(r"без\s+зеркал", txt):
        return "Без зеркала"
    if re.search(r"с\s+зеркал", txt) or "зеркальн" in txt:
        return "С зеркалом"
    group = [r for r in (rows or []) if r]
    if group:
        has_without = any(re.search(r"без\s+зеркал", _kit_text(r)) for r in group)
        has_word = any("зеркал" in _kit_text(r) for r in group)
        if has_without and len(group) > 1:
            return "С зеркалом"
        if has_word:
            return "С зеркалом"

        def model_of(r):
            gi = _kit_item(r)
            return str(((gi or {}).get("a") or {}).get("Название модели") or _kit_title(r, gi) or "").lower().replace("ё", "е")
        model = model_of(row)
        if re.search(r"квадро|трио", model) and len(group) == 2:
            same = all(m == model or ("квадро" in model and "квадро" in m) or ("трио" in model and "трио" in m)
                       for m in map(model_of, group))
            prices = [p for p in map(_kit_price, group) if p]
            if same and len(prices) == 2 and prices[0] != prices[1]:
                return "С зеркалом" if _kit_price(row) == max(prices) else "Без зеркала"
    if "зеркал" in txt:
        return "С зеркалом"
    return ""


def _kit_mirror_rank(row, rows):
    lab = _kit_mirror_label(row, rows).lower()
    if "с зеркал" in lab and "без" not in lab:
        return 1
    return 2 if "без зеркал" in lab else 3


def _kit_filling_rank(row):
    txt = _kit_text(row)
    if re.search(r"с\s+полк", txt) or "полками" in txt:
        return 1
    return 2 if ("платян" in txt or "штанг" in txt) else 3


def _kit_size(row):
    item = _kit_item(row)
    a = (item or {}).get("a") or {}
    v = _kit_num(a.get("Ширина спального места")) or _kit_num(a.get("Ширина"))
    if v:
        return v
    nums = [_kit_num(x) for x in re.findall(r"(?:^|\D)(\d{2,4})(?=\D|$)", str(_kit_title(row, item)))]
    nums = [x for x in nums if x]
    return nums[-1] if nums else 0


def _kit_cmp_desc(a, b):          # kitVariantSortDesc
    ma, mb = _kit_mirror_rank(a, [a, b]), _kit_mirror_rank(b, [a, b])
    if ma != mb:
        return ma - mb
    ea, eb = _kit_filling_rank(a), _kit_filling_rank(b)
    if ea != eb:
        return ea - eb
    sa, sb = _kit_size(a), _kit_size(b)
    if sb != sa:
        return -1 if sb < sa else 1
    pa, pb = _kit_price(a), _kit_price(b)
    if pb != pa:
        return -1 if pb < pa else 1
    return (1 if b.get("required") else 0) - (1 if a.get("required") else 0)


def _kit_row_qty(row, stock):
    item = _kit_item(row)
    return qty_of(item, stock) if item else 0


def _kit_choose(rows, stock):     # chooseKitCompRow (без ручного выбора покупателя)
    rows = [r for r in rows if r]
    if not rows:
        return None
    for r in rows:
        if r.get("defaultPick") is True:
            return r

    def cmp(a, b):
        ma, mb = _kit_mirror_rank(a, rows), _kit_mirror_rank(b, rows)
        if ma != mb:
            return ma - mb
        ea, eb = _kit_filling_rank(a), _kit_filling_rank(b)
        if ea != eb:
            return ea - eb
        return _kit_cmp_desc(a, b)
    srt = sorted(rows, key=functools.cmp_to_key(cmp))
    if len({_kit_mirror_label(r, rows) for r in rows} - {""}) > 1:
        return srt[0]
    for r in srt:
        if _kit_row_qty(r, stock) >= max(1, int(_kit_num(r.get("qty")) or 1)):
            return r
    return next((r for r in rows if r.get("required")), None) or srt[0]


def _kit_sources(row, stock):     # kitRowStockSources
    item = _kit_item(row)
    if item:
        return [("id:" + str(item.get("id")), qty_of(item, stock))]
    arts = row.get("component_arts") or []
    if arts:
        out = []
        for a in arts:
            k = norm_art(a)
            if not k:
                return None
            out.append(("art:" + k, stock_by_art(k, stock)))
        return out
    k = norm_art(row.get("module_art") or row.get("supplier_art") or row.get("art") or "")
    return [("art:" + k, stock_by_art(k, stock))] if k else None


def kit_stock(it, stock):
    """(в наличии?, сколько комплектов) по составу; None — у товара нет состава."""
    comp = KIT_COMP.get(str(it.get("id"))) or []
    if not comp:
        return None
    buckets = {}
    for idx, row in enumerate(comp):
        if not row:
            continue
        o = row.get("order")
        key = str(o) if o not in (None, "") else str(idx + 1)
        buckets.setdefault(key, {"order": _kit_num(o) or idx + 1, "rows": []})["rows"].append(row)
    active = [_kit_choose(g["rows"], stock) or g["rows"][0]
              for g in sorted(buckets.values(), key=lambda g: g["order"])]
    total = units = 0
    need_by, parsed, missing = {}, [], False
    for row in active:
        need = max(1, int(_kit_num(row.get("qty")) or 1))
        total += need
        src = _kit_sources(row, stock)
        parsed.append((need, src))
        if src:
            for k, q in src:
                rec = need_by.setdefault(k, [0, q])
                rec[0] += need
        else:
            missing = True
    if not total:
        return None
    kits = min((q // n for n, q in need_by.values()), default=0)
    if missing:
        kits = 0
    free = {k: q for k, (n, q) in need_by.items()}
    for need, src in parsed:
        if not src:
            continue
        m = min([need] + [free.get(k, 0) for k, _ in src])
        if m > 0:
            units += m
            for k, _ in src:
                free[k] -= m
    have = units == total
    kit_stock.last = (units, total)          # для Авито: «часть модулей есть» (добавлено здесь)
    return have, (max(0, kits) if have else 0)


