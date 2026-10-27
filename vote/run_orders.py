"""
スプレッドシートの買い注文(ipat_orders)を拾って、即PATで購入する(GitHub Actions用)。

  IPAT_MODE=off      何もしない(既定)
  IPAT_MODE=dry_run  購入確認画面まで操作して停止(購入しない)。Actionsの接続元でログインできるかの確認用
  IPAT_MODE=live     実購入

  PYTHONPATH=. python -m vote.run_orders [--until HH:MM]

安全装置:
  - 発走4分前を過ぎた注文は購入せず expired にする(ログインや画面操作に時間がかかるため)
  - 二重投票防止と金額上限は ipat_vote.py(購入記録は PURCHASE_STORE=sheet でシートに保存)
  - live は PURCHASE_STORE=sheet でなければ起動しない(ランナーは毎回まっさらで、CSVだと記録が消える)
  - ログに馬番・金額は出さない(公開リポジトリ)。race_id と結果だけ
"""
import argparse
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

from vote import order_queue

START_DEADLINE_MIN = 4   # 発走の何分前までに処理を始められなければ見送る
POLL_SEC = 15


def execute(race_id, orders, mode):
    """1レース分の注文を購入。戻り値: (status, note)"""
    from vote import ipat_vote
    race_date = datetime.strptime(orders[0][1]["date"], "%Y-%m-%d").date()
    stakes = [(int(d["horsenum"]), int(d["yen"])) for _, d in orders]
    try:
        res = ipat_vote.place_bets(race_id, race_date, stakes, live=(mode == "live"))
        return res["status"], ""
    except Exception as e:  # noqa: BLE001  購入状況が不明な失敗も含む。購入記録の "uncertain" が再購入を止める
        return "failed", f"{type(e).__name__}: {str(e)[:150]}"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default=None, help="HH:MM。これを過ぎたら終了(6時間制限の分割用)")
    args = ap.parse_args()
    mode = os.environ.get("IPAT_MODE", "off")
    if mode not in ("off", "dry_run", "live"):
        sys.exit(f"IPAT_MODE が不正: {mode}")
    if mode == "live" and os.environ.get("PURCHASE_STORE", "csv") != "sheet":
        sys.exit("live には PURCHASE_STORE=sheet が必要です")
    until = None
    if args.until:
        until = datetime.combine(datetime.now().date(), datetime.strptime(args.until, "%H:%M").time())
    print(f"IPAT_MODE={mode} / 終了={until or '手動停止まで'}")
    if mode == "off":
        print("off のため何もしません")
        return

    pool, running = ThreadPoolExecutor(max_workers=2), {}
    try:
        while not (until and datetime.now() >= until):
            now = datetime.now()
            by_race = defaultdict(list)
            try:
                for row, d in order_queue.load_pending():
                    by_race[d["race_id_yahoo"]].append((row, d))
            except Exception as e:  # noqa: BLE001  シートの一時的な通信エラーは次の周回で再試行
                print(f"注文の読み込み失敗: {type(e).__name__}")
            for rid, orders in by_race.items():
                if rid in running:
                    continue
                post = datetime.fromisoformat(orders[0][1]["post_dt"])
                rows = [r for r, _ in orders]
                if now > post - timedelta(minutes=START_DEADLINE_MIN):
                    order_queue.set_status(rows, "expired", "締切までに処理できず見送り")
                    print(f"[{rid}] expired")
                    continue
                fut = pool.submit(execute, rid, orders, mode)
                running[rid] = (fut, rows)
            for rid in list(running):
                fut, rows = running[rid]
                if fut.done():
                    status, note = fut.result()
                    order_queue.set_status(rows, status, note)
                    print(f"[{rid}] {status}")
                    del running[rid]
            time.sleep(POLL_SEC)
    finally:
        pool.shutdown(wait=True)
    print("終了")


if __name__ == "__main__":
    main()
