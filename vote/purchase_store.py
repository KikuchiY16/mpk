"""
即PAT購入記録の保存先を切り替える薄い層(2026-10-24)。

ipat_vote.py は二重投票防止と1日上限のために購入記録を ipat_purchase_log.csv(ローカル)に
持っている。GitHub Actionsのランナーは毎回まっさらな環境で記録が消え、同じレースを2回買ったり
1日上限を超えたりしかねない。そこで記録の読み書きだけをここに切り出し、環境変数で
Googleスプレッドシートにも切り替えられるようにする。

  PURCHASE_STORE=csv     (既定) ローカルCSV。従来どおり。自宅PCではこのままでよい
  PURCHASE_STORE=sheet   スプレッドシートのタブ ipat_purchases に保存
                         必要: GOOGLE_SERVICE_ACCOUNT_JSON(鍵JSONの中身) と ORDERS_SHEET_ID(非公開シート)
                         (どちらもGitHub Secretsに入れる。コードには書かない)

インターフェースは ipat_vote.py の既存関数と同じ形:
    load_purchases()           -> DataFrame[timestamp,date,race_id_yahoo,horsenum,yen,live,status]
    append_purchases(rows)     -> None   rows: list[dict] (同じ列)

ipat_vote.py への組み込み(2行):
    _load_purchase_log()   の本体を  return purchase_store.load_purchases()
    _append_purchase_log() の本体を  purchase_store.append_purchases(rows)
シート版の失敗(通信エラー等)は握りつぶさず例外にする: 記録が読めないまま購入すると二重投票防止が
効かなくなるため、読めなければ購入しない(fail closed)。
"""
import json
import os

import pandas as pd

COLUMNS = ["timestamp", "date", "race_id_yahoo", "horsenum", "yen", "live", "status"]
HERE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.environ.get("IPAT_PURCHASE_LOG", os.path.join(HERE, "ipat_purchase_log.csv"))
SHEET_TAB = "ipat_purchases"


def _mode():
    return os.environ.get("PURCHASE_STORE", "csv").lower()


def _normalize(df):
    """シートから読むと全て文字列になるので、ipat_vote.check_limits が期待する型に直す。"""
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df = df.reindex(columns=COLUMNS)
    df["race_id_yahoo"] = df["race_id_yahoo"].astype(str)
    df["date"] = df["date"].astype(str)
    df["yen"] = pd.to_numeric(df["yen"], errors="coerce").fillna(0)
    df["horsenum"] = pd.to_numeric(df["horsenum"], errors="coerce")
    df["live"] = df["live"].astype(str).str.lower().isin(["true", "1", "yes"])
    return df


def _open_sheet():
    import gspread
    key, sheet_id = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON"), os.environ.get("ORDERS_SHEET_ID")
    if not key or not sheet_id:
        raise RuntimeError("PURCHASE_STORE=sheet には GOOGLE_SERVICE_ACCOUNT_JSON と ORDERS_SHEET_ID が必要です")
    sh = gspread.service_account_from_dict(json.loads(key)).open_by_key(sheet_id)
    try:
        return sh.worksheet(SHEET_TAB)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_TAB, rows=1000, cols=len(COLUMNS))
        ws.update([COLUMNS], "A1")
        return ws


def load_purchases():
    if _mode() == "sheet":
        records = _open_sheet().get_all_records()   # 失敗は例外のまま(fail closed)
        return _normalize(pd.DataFrame(records))
    if not os.path.exists(CSV_PATH):
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(CSV_PATH, dtype={"race_id_yahoo": str})


def append_purchases(rows):
    if not rows:
        return
    df = pd.DataFrame(rows).reindex(columns=COLUMNS)
    if _mode() == "sheet":
        ws = _open_sheet()
        ws.append_rows(df.astype(str).values.tolist(), value_input_option="RAW")
        return
    header = not os.path.exists(CSV_PATH)
    df.to_csv(CSV_PATH, mode="a", header=header, index=False)
