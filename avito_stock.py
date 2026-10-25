# -*- coding: utf-8 -*-
"""Количество «N шт. в наличии» на Авито (заказчик 24.10: «чтобы работало по количеству правильно»).

В файле автозагрузки у мебели поля «количество» нет — Авито принимает его только через API
«Управление остатками» (/stock-management/1/stocks), и только у объявлений с «Авито Доставкой» (~1311 из 3440).
Каждую ночь после avito_feed.py (тот же робот, .github/workflows/avito-feed.yml):
  количество = остаток склада Модерн по логике сайта (товар / купе «корпус+фасад» / комплект — сколько полных /
  матрас — максимум по размерам); нет на складе → 1 (решение заказчика 24.10: «вариант 1» — объявление не гаснет,
  метка и текст говорят «Под заказ»); диваны → 1 (салон подключим отдельно).
Шлёт только изменившиеся. Защита: остатки старше 3 дней — ничего не меняет.
Ключи — переменные AVITO_CLIENT_ID / AVITO_CLIENT_SECRET (секреты GitHub) или ~/.secrets/avito.json.

python avito_stock.py --data _data                 — план (что поменяется), без записи
python avito_stock.py --data _data --write         — отправить на Авито
python avito_stock.py --data _data --refresh-ids   — заново узнать, у каких объявлений есть количество
"""
import argparse
import collections
import datetime
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import avito_feed as F  # noqa: E402
import kit_logic as K  # noqa: E402

API = "https://api.avito.ru"
IDS = os.path.join(HERE, "base", "avito-stock-ids.json")


def token():
    cid, sec = os.environ.get("AVITO_CLIENT_ID"), os.environ.get("AVITO_CLIENT_SECRET")
    if not (cid and sec):
        d = json.load(open(os.path.expanduser("~/.secrets/avito.json"), encoding="utf-8-sig"))
        cid, sec = d["client_id"], d["client_secret"]
    body = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": cid.strip(),
                                   "client_secret": sec.strip()}).encode()
    req = urllib.request.Request(API + "/token/", data=body,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())["access_token"]


def call(tok, path, body, method="POST", tries=4):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method=method,
                                 headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(req, timeout=90).read().decode())
        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504) and i < tries - 1:
                time.sleep(20 * (i + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if i < tries - 1:
                time.sleep(10)
                continue
            raise


def info(tok, ids):
    """Остатки по объявлениям; пачка с объявлением без «количества» отвечает 500 — делим пополам."""
    if not ids:
        return []
    try:
        r = call(tok, "/stock-management/1/info", {"item_ids": ids}, tries=2)
        if not isinstance(r, dict) or "stocks" not in r:          # 25.10: Авито ответил без «stocks» — делим дальше
            raise ValueError("нет stocks")
        return r["stocks"]
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError):
        if len(ids) == 1:
            return []
        h = len(ids) // 2
        return info(tok, ids[:h]) + info(tok, ids[h:])


def qty_for(n, M, stock):
    """Сколько штук показать на Авито (логика сайта, как в avito_feed.decide)."""
    meta = M.get(n) or {}
    it = K.CAT_BY_ID.get(n)
    if meta.get("kind") == "sofa" or (it and it.get("c") == "Диваны"):
        return 1
    if F.MATTRESS.get(n):
        q = 0
        for v in F.MATTRESS[n].get("variants") or []:
            arts = [x.get("a") for x in (v.get("arts") or [])] or [v.get("art")]
            q = max(q, sum(K.stock_by_art(a, stock) for a in arts if a))
        return max(1, q)
    if F.COUPE.get(n):
        return max(1, F.coupe_qty(F.COUPE[n], stock))
    if it and K.KIT_COMP.get(n):
        have, kits = K.kit_stock(it, stock)
        return max(1, kits if have else 0)
    q = K.qty_of(it, stock) if it else K.stock_by_art(meta.get("art"), stock)
    return max(1, q)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(HERE, "_data"))
    ap.add_argument("--map", default=os.path.join(HERE, "base", "avito-map.json"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--refresh-ids", action="store_true")
    ap.add_argument("--max-age", type=int, default=3)
    a = ap.parse_args()
    stock, sdate = F.load_data(a.data)
    today = datetime.datetime.now(F.STERL).date()
    if (today - sdate).days > a.max_age:
        print(f"!! остатки от {sdate:%d.%m.%Y} устарели — количество не трогаю")
        sys.exit(2)
    M = json.load(open(a.map, encoding="utf-8"))["map"]
    tok = token()
    done_today = os.path.exists(IDS) and json.load(open(IDS, encoding="utf-8")).get("date") == str(today)
    if (a.refresh_ids and not done_today) or not os.path.exists(IDS):   # почасовые прогоны: обновляем раз в день
        all_ids = [int(n) for n in M]
        got = []
        for i in range(0, len(all_ids), 50):
            got += info(tok, all_ids[i:i + 50])
        ids = sorted(x["item_id"] for x in got)
        json.dump({"date": str(today), "ids": ids}, open(IDS, "w", encoding="utf-8"))
        print(f"объявлений с количеством: {len(ids)} (из {len(all_ids)}) → {IDS}")
    ids = [i for i in json.load(open(IDS, encoding="utf-8"))["ids"] if str(i) in M]
    cur = {}
    for i in range(0, len(ids), 50):
        for x in info(tok, ids[i:i + 50]):
            cur[x["item_id"]] = x
    plan, why = [], collections.Counter()
    for i in ids:
        if i not in cur:
            continue
        q = qty_for(str(i), M, stock)
        c = cur[i]
        why["на складе" if q > 1 or (q == 1 and K.qty_of(K.CAT_BY_ID.get(str(i)), stock) == 1) else "1 (под заказ/диван)"] += 1
        if c.get("is_unlimited") or c.get("quantity") != q:
            plan.append({"item_id": i, "quantity": q})
    print(f"остатки от {sdate:%d.%m.%Y}; объявлений с количеством: {len(cur)}; поменять: {len(plan)}; {dict(why)}")
    for p in plan[:15]:
        print(f"   {p['item_id']}: {cur[p['item_id']].get('quantity')}"
              f"{' (без огранич.)' if cur[p['item_id']].get('is_unlimited') else ''} → {p['quantity']}")
    if not a.write:
        print("План. Отправить: --write")
        return
    ok = bad = 0
    for i in range(0, len(plan), 50):
        r = call(tok, "/stock-management/1/stocks", {"stocks": plan[i:i + 50]}, method="PUT")
        for x in r.get("stocks", []):
            ok += bool(x.get("success"))
            bad += not x.get("success")
    print(f"отправлено: успешно {ok}, отказ {bad}")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
