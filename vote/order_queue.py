"""
買い注文のキュー(Googleスプレッドシートのタブ ipat_orders)。

予測・判定を行う側(自宅PCなど)は、購入したい買い目をここに書くだけ。このリポジトリの Actions
(vote.yml → run_orders.py)が拾って、締切前に即PATで購入する。判定ロジックはこのリポジトリに無い。

列: order_id, created_at, date, race_id_yahoo, post_dt, horsenum, yen, status, note, updated_at
  status: 空=未処理 / dry_run / purchased / failed / expired
認証は環境変数のみ: GOOGLE_SERVICE_ACCOUNT_JSON(鍵JSONの中身), ORDERS_SHEET_ID(**非公開**のスプレッドシート。
予想サイトが読む公開シートとは別にすること。買い目・金額が誰でも読めてしまうため)。

判定側からの使い方(例):
    from order_queue import enqueue
    enqueue(race_id="2605040811", race_date="2026-10-24", post_dt="2026-10-24T15:45:00", bets=[(7, 100)])
"""
import json
import os
import uuid
from datetime import datetime

COLUMNS = ["order_id", "created_at", "date", "race_id_yahoo", "post_dt", "horsenum", "yen", "status", "note", "updated_at"]
TAB = "ipat_orders"


def _ws():
    import gspread
    key, sid = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"), os.environ.get("ORDERS_SHEET_ID")
    if not key or not sid:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON と ORDERS_SHEET_ID が必要です")
    sh = gspread.service_account_from_dict(json.loads(key)).open_by_key(sid)
    try:
        return sh.worksheet(TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB, rows=1000, cols=len(COLUMNS))
        ws.update([COLUMNS], "A1")
        return ws


def enqueue(race_id, race_date, post_dt, bets):
    """bets: [(馬番, 金額円), ...]。同じレースの重複投入は呼び出し側で避けること(二重購入は
    購入記録側でも止まるが、キューに二重に入れない方がよい)。戻り値: 追加した行数。"""
    now = datetime.now().isoformat(timespec="seconds")
    rows = [[uuid.uuid4().hex[:12], now, str(race_date), str(race_id), str(post_dt), int(h), int(y), "", "", now]
            for h, y in bets]
    _ws().append_rows(rows, value_input_option="RAW")
    return len(rows)


def load_pending():
    """未処理(statusが空)の注文を [(行番号, dict)] で返す。行番号は1始まり(ヘッダが1行目)。"""
    values = _ws().get_all_values()
    out = []
    for i, row in enumerate(values[1:], start=2):
        d = dict(zip(COLUMNS, row + [""] * (len(COLUMNS) - len(row))))
        if d["order_id"] and not d["status"]:
            out.append((i, d))
    return out


def set_status(row_numbers, status, note=""):
    """指定行の status / note / updated_at を更新する。"""
    ws = _ws()
    now = datetime.now().isoformat(timespec="seconds")
    ws.batch_update([{"range": f"H{r}:J{r}", "values": [[status, note[:200], now]]} for r in row_numbers])
