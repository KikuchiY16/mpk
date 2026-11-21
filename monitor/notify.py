"""LINE通知(Messaging API の push)。同じ通知を1日に何度も送らないよう、シートのタブ notices に送信済みを記録する。
  LINE_ACCESS_TOKEN / LINE_TO_USER_ID : GitHub Secrets。どちらかが無ければ送らない。
通知文に動画URL・金額などは入れない(公開リポジトリのログに出るため、通知内容は定型文のみ)。"""
import json
import os

import requests

PUSH_URL = "https://api.line.me/v2/bot/message/push"
TAB = "notices"


def _sent_before(key):
    import gspread
    gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    sh = gc.open_by_key(os.environ["SHEET_ID"])
    try:
        return key in set(sh.worksheet(TAB).col_values(1))
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=TAB, rows=500, cols=2)
        ws.update([["key", "sent_at"]], "A1")
        return False


def _mark(key):
    import gspread
    from datetime import datetime
    gc = gspread.service_account_from_dict(json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]))
    gc.open_by_key(os.environ["SHEET_ID"]).worksheet(TAB).append_rows(
        [[key, datetime.now().isoformat(timespec="seconds")]], value_input_option="RAW")


def send_once(key, text):
    """key 単位で1回だけ LINE を送る。戻り値: 送った=True。未設定・送信済み・失敗は False(例外は出さない)。"""
    token, to = os.environ.get("LINE_ACCESS_TOKEN"), os.environ.get("LINE_TO_USER_ID")
    if not token or not to:
        print("[LINE] 未設定のため通知しません")
        return False
    try:
        if _sent_before(key):
            return False
        r = requests.post(PUSH_URL, headers={"Authorization": f"Bearer {token}"},
                          json={"to": to, "messages": [{"type": "text", "text": text}]}, timeout=10)
        if r.status_code != 200:
            print(f"[LINE] 送信失敗 HTTP {r.status_code}")
            return False
        _mark(key)
        print("[LINE] 通知しました")
        return True
    except Exception as e:  # noqa: BLE001  通知の失敗で監視を止めない
        print(f"[LINE] 例外: {type(e).__name__}")
        return False
