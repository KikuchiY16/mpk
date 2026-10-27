"""
JRA即PAT(https://www.ipat.jra.go.jp/、PC版)をPlaywrightでブラウザ操作して
複勝を投票するモジュール(2026-10-23追加)。

vote_api.py(masters.netkeiba.comのコンペ専用API)とは別物で、こちらは
**実際のお金が動く本物のJRA投票**。4c_chaos_gate_decision.pyの判定結果を
自動で買う用途を想定している(--ipat / --ipat_live、同スクリプト参照)。

【前提・注意】
- 即PAT約定第27条12号(JRAの包括的裁量でサービス停止しうる条項)があり、
  ブラウザ自動操作はJRAが公式に提供している手段ではない。アカウント停止リスクは
  利用者自身の判断で負うこと(2026-09-26に一度「手動投票」方針にした経緯あり、
  0_fetch_schedule.pyのdocstring参照)。
- 下のSELECTORSは作成時点(受付時間外で実画面を確認できなかった)のPC版即PATの
  構造に基づく推定。**初回は必ず --headed のドライラン(購入ボタンを押さない)で
  全画面を目視確認し、ずれていたらSELECTORSだけ直すこと**。--pause を付けると
  Playwright Inspectorで止まるので、セレクタを実画面で確かめられる。
- 二重投票防止: 即PATはnetkeibaのAPIと違い「同じレースに再投票=上書き」ではなく
  「追加購入」になる。タスクの再実行などで同じレースを2回買わないよう、
  ipat_purchase_log.csvに同日・同race_idの購入済み記録があれば投票しない。
- 金額上限: 1レース上限・1日上限を超える投票は拒否する(下の定数)。

初回セットアップ:
  pip install playwright
  python -m playwright install chromium
  local_secrets.py に IPAT_INET_ID / IPAT_SUBSCRIBER_NO / IPAT_PIN / IPAT_PARS を追記
  (環境変数 IPAT_INET_ID 等でも可)

単体での動作確認(ドライラン、ブラウザ表示あり、購入はしない):
  python ipat_vote.py --race_id 2605040811 --horse 7:100 --headed
実際に購入:
  python ipat_vote.py --race_id 2605040811 --horse 7:100 --live
"""
import argparse
import os
import re
import sys
from datetime import date as _date, datetime as _dt

import pandas as pd

sys.path.append(os.path.dirname(__file__))
import common

try:
    import local_secrets as _secrets
except ImportError:
    _secrets = None


def _secret(name):
    return getattr(_secrets, name, "") or os.environ.get(name, "")


IPAT_URL = "https://www.ipat.jra.go.jp/"

HERE = os.path.dirname(__file__)
PURCHASE_LOG_PATH = os.path.join(HERE, "ipat_purchase_log.csv")
SCREENSHOT_DIR = os.path.join(HERE, "ipat_screens")

# 安全装置(円)。超える投票は送らずに例外にする。
MAX_PER_RACE_YEN = 3000
MAX_PER_DAY_YEN = 20000

# common.decode_race_id()のplace_jra_code -> 即PATの場名表示
VENUE_NAMES = {
    1: "札幌", 2: "函館", 3: "福島", 4: "新潟", 5: "東京",
    6: "中山", 7: "中京", 8: "京都", 9: "阪神", 10: "小倉",
}

# 即PAT(PC版)の画面要素。実画面とずれていたらここだけ直す。
SELECTORS = {
    # ログイン1画面目
    "inet_id": 'input[name="inetid"]',
    # 2026-10-24実画面で確認: 文字なしの<a href="#" onclick="send()" title="ログイン">
    "login1_submit": 'a[title="ログイン"]',
    # ログイン2画面目
    "subscriber_no": 'input[name="i"]',
    "pin": 'input[name="p"]',
    "pars": 'input[name="r"]',
    # 1画面目と同じくtitle属性だけのリンクの可能性があるため両方を候補にする(未確認)
    "login2_submit": 'a[title*="ネット投票メニュー"], a:has-text("ネット投票メニュー"), '
                     'button:has-text("ネット投票メニュー")',
    # ログイン後のお知らせ画面(出る場合のみ)
    "notice_ok": 'button:has-text("OK"):visible',  # 非表示のOKボタンが画面内に常にある
    # メニュー
    "menu_basic_bet": 'button:has-text("通常")',
    "purchase_limit": "購入限度額",  # この文字を含む表示中の要素の近くにある「NNN円」を読む
    # 通常投票画面(2026-10-24実画面: 式別selectのみで方式欄は無い。馬番表の
    # 1列目が馬番、2列目が選択チェック。右側に「金額 [ ] 00円」「展開セット」
    # 「セット」「入力終了」)
    # 2026-10-24実画面: "#bet-basic-type"で複勝を選べた。素の"select"にすると
    # 画面内の別のselectを掴んで失敗したため、IDを優先し、無ければ「複勝」の
    # 選択肢を持つselectを探す。
    "bet_type_select": "#bet-basic-type",
    "bet_type_select_fallback": 'select:has(option:text-is("複勝"))',
    # 1列目の文字が馬番と一致する行(XPath)
    "horse_row": "xpath=//tr[normalize-space(td[1])='{horsenum}']",
    # 2026-10-24実画面: 金額欄(100円単位)はng-model=vm.nUnit。同じng-modelの非表示欄も
    # あるため:visibleで絞る(「金額」の文字で探すと購入予定リスト側のvm.cAmountを掴んだ)
    "unit_input": 'input[ng-model="vm.nUnit"]:visible',
    "set_button": re.compile(r"^\s*セット\s*$"),       # 「展開セット」に当たらないよう完全一致
    "finish_input_button": re.compile(r"^\s*入力終了\s*$"),
    # 購入確認(再確認)画面: 入力終了後に表示される合計金額欄(実画面で存在を確認、表示後は未確認)
    "total_amount_input": 'input[ng-model="vm.cAmountTotal"]:visible',
    "purchase_button": 'button:has-text("購入する"):visible',
    "confirm_ok": 'button:has-text("OK"):visible',
    # 受付時間外ページの判定文言
    "closed_text": "投票受付時間外",
}

STEP_TIMEOUT_MS = 15000


class IpatError(RuntimeError):
    pass


# ---------------------------------------------------------------- 安全装置

def _load_purchase_log():
    # 2026-10-24: 保存先を purchase_store に切り出した(PURCHASE_STORE=sheet でスプレッドシート、
    # 既定はこれまでと同じローカルCSV)。GitHub Actionsのランナーは毎回まっさらでCSVが消えるため。
    # シートが読めない時は例外のまま(読めずに買うと二重投票防止が効かないので購入しない)。
    import purchase_store
    return purchase_store.load_purchases()


def _append_purchase_log(rows):
    import purchase_store
    purchase_store.append_purchases(rows)


def check_limits(race_id, race_date, horse_stakes):
    """投票前チェック。問題があればIpatErrorを投げる。"""
    if not horse_stakes:
        raise IpatError("買い目が空です")
    for horsenum, yen in horse_stakes:
        if yen <= 0 or yen % 100 != 0:
            raise IpatError(f"馬番{horsenum}: 金額{yen}円は100円単位の正の値ではありません")
    race_total = sum(yen for _, yen in horse_stakes)
    if race_total > MAX_PER_RACE_YEN:
        raise IpatError(f"1レース合計{race_total:,}円が上限{MAX_PER_RACE_YEN:,}円を超えています")

    log = _load_purchase_log()
    # "uncertain"(購入するを押した後に失敗)も買えた可能性があるので購入済み扱い
    done = log[(log["date"] == str(race_date)) & (log["live"] == True)  # noqa: E712
               & (log["status"].isin(["purchased", "uncertain"]))]
    if (done["race_id_yahoo"] == str(race_id)).any():
        raise IpatError(f"race_id={race_id}は本日すでに購入済みです(二重投票防止、{PURCHASE_LOG_PATH})")
    day_total = float(done["yen"].sum()) + race_total
    if day_total > MAX_PER_DAY_YEN:
        raise IpatError(f"本日の購入合計が{day_total:,.0f}円になり、1日上限{MAX_PER_DAY_YEN:,}円を超えます")


# ---------------------------------------------------------------- ブラウザ操作

def _shot(page, race_id, step):
    os.makedirs(SCREENSHOT_DIR, exist_ok=True)
    path = os.path.join(SCREENSHOT_DIR, f"{_dt.now():%Y%m%d_%H%M%S}_{race_id}_{step}.png")
    try:
        page.screenshot(path=path, full_page=True)
    except Exception as e:
        print(f"  スクリーンショット失敗({step}): {e}")
    return path


def _login(page):
    creds = {k: _secret(k) for k in ("IPAT_INET_ID", "IPAT_SUBSCRIBER_NO", "IPAT_PIN", "IPAT_PARS")}
    missing = [k for k, v in creds.items() if not v]
    if missing:
        raise IpatError(f"即PATのログイン情報が未設定です: {missing}(local_secrets.pyか環境変数に設定)")

    page.goto(IPAT_URL)
    if SELECTORS["closed_text"] in page.content():
        raise IpatError("即PATは現在受付時間外です")

    page.fill(SELECTORS["inet_id"], creds["IPAT_INET_ID"])
    page.locator(SELECTORS["login1_submit"]).first.click()
    page.wait_for_selector(SELECTORS["subscriber_no"])
    page.fill(SELECTORS["subscriber_no"], creds["IPAT_SUBSCRIBER_NO"])
    page.fill(SELECTORS["pin"], creds["IPAT_PIN"])
    page.fill(SELECTORS["pars"], creds["IPAT_PARS"])
    page.locator(SELECTORS["login2_submit"]).first.click()

    # 2026-10-24(ユーザー指摘: 通常投票を押すまでが遅い): 以前はnetworkidle待ち
    # (即PATは裏で通信し続けるので長引く)+お知らせOKを毎回3秒待っていた。
    # メニューかお知らせのどちらかが表示された時点で次へ進む。
    menu = page.locator(SELECTORS["menu_basic_bet"]).first
    notice = page.locator(SELECTORS["notice_ok"]).first
    menu.or_(notice).first.wait_for()
    if notice.is_visible():
        notice.click()
        menu.wait_for()


def _select_race(page, venue_name, racenum):
    page.locator(SELECTORS["menu_basic_bet"]).first.click()
    # 場名ボタンは「東京（土）」のように曜日付き。場名で部分一致させる。
    page.locator("button", has_text=venue_name).first.click()
    # 2026-10-24実画面: レースボタンは"11R"だけでなく発走時刻等も含むため完全一致では
    # 当たらなかった。直前が数字でない"{n}R"で探す("1R"が"11R"に当たらないように)。
    race_button = page.locator("button, a", has_text=re.compile(rf"(^|[^0-9]){racenum}R"))
    try:
        race_button.first.click()
    except Exception:
        _dump_buttons(page, f"{racenum}Rのボタンが見つかりません")
        raise


def _dump_buttons(page, reason):
    """セレクタ調整用: 画面上のボタン・リンクの文字と入力欄の属性を一覧表示する。"""
    if os.environ.get("GITHUB_ACTIONS"):  # 公開ログに画面の中身を出さない
        print(f"[即PAT] {reason}")
        return
    print(f"[即PAT] {reason}。画面上のボタン一覧:")
    try:
        for t in page.locator("button, a").all_inner_texts()[:80]:
            t = " ".join(t.split())
            if t:
                print(f"    {t!r}")
        print("[即PAT] 入力欄一覧:")
        infos = page.locator("input, select").evaluate_all(
            "els => els.map(e => [e.tagName, e.type, e.name, e.id, e.getAttribute('ng-model'),"
            " e.offsetParent !== null])")
        for info in infos[:80]:
            print(f"    tag={info[0]} type={info[1]} name={info[2]} id={info[3]} ng-model={info[4]} visible={info[5]}")
    except Exception as e:
        print(f"    (一覧取得に失敗: {e})")


def _step(page, name, fn):
    """1操作を実行し、失敗したら画面の要素一覧を出してから例外を投げ直す。"""
    try:
        return fn()
    except Exception:
        _dump_buttons(page, f"「{name}」で失敗しました")
        raise


def _click_button(page, text_pattern):
    page.locator("button, a, input[type=button]", has_text=text_pattern).first.click()


def _check_horse(page, horsenum):
    row = page.locator(SELECTORS["horse_row"].format(horsenum=int(horsenum))).first
    # 見た目のチェック欄はlabel(中のinputは非表示)のことが多いのでlabelを優先する
    label = row.locator("label")
    if label.count() > 0:
        try:
            label.first.click(timeout=3000)
            return
        except Exception:
            pass
    # 非表示のinputはPlaywrightのcheck()が押せないため、JSでclickしてchangeを発火させる
    row.locator("input[type=checkbox]").first.evaluate("e => e.click()")


def _select_place_type(page):
    primary = page.locator(SELECTORS["bet_type_select"])
    try:
        primary.first.wait_for(timeout=5000)
        sel = primary.first
    except Exception:
        sel = page.locator(SELECTORS["bet_type_select_fallback"]).first
    sel.select_option(label="複勝")


def _enter_place_bets(page, horse_stakes):
    _step(page, "式別=複勝", lambda: _select_place_type(page))
    for horsenum, yen in horse_stakes:
        _step(page, f"馬番{horsenum}を選択", lambda: _check_horse(page, horsenum))
        # 金額欄は「00円」の前に入れる100円単位の口数
        _step(page, "金額入力", lambda: page.locator(SELECTORS["unit_input"]).first.fill(str(int(yen) // 100)))
        _step(page, "セット", lambda: _click_button(page, SELECTORS["set_button"]))
    _step(page, "入力終了", lambda: _click_button(page, SELECTORS["finish_input_button"]))


# 表示中の「購入限度額」から祖先を最大6段たどり、ラベル以降で最初の「NNN円」を返す。
# 2026-10-24実画面(投票結果ダイアログ): ラベル列「購入限度額/購入可能件数」と
# 値列「900円/8,999件」が別要素に分かれていて、ラベルの親だけでは値が取れなかった。
_PURCHASE_LIMIT_JS = """
els => {
  for (const e of els) {
    if (e.getClientRects().length === 0) continue;  // 非表示のコピーは無視
    let a = e;
    for (let i = 0; i < 6 && a; i++, a = a.parentElement) {
      const t = a.innerText || "";
      const k = t.indexOf("購入限度額");
      if (k < 0) continue;
      const m = t.slice(k).match(/([0-9,]+)\\s*円/);
      if (m) return m[1];
    }
  }
  return null;
}
"""


def _read_purchase_limit(page, retries=4, interval_ms=500):
    """表示中の購入限度額(円)を読む。読めなければNone。
    0円は金額の描画前の可能性があるため、少し待って読み直す。"""
    value = None
    for i in range(retries):
        try:
            raw = page.get_by_text(SELECTORS["purchase_limit"]).evaluate_all(_PURCHASE_LIMIT_JS)
        except Exception:
            raw = None
        if raw is None:
            return None  # 表示されていない画面では待たない(ログインを遅くしないため)
        value = int(raw.replace(",", ""))
        if value > 0:
            return value
        if i < retries - 1:
            page.wait_for_timeout(interval_ms)
    return value


def _check_purchase_limit(page, result, total, where):
    """購入限度額を読んでresultに入れ、合計に足りなければIpatErrorを投げる。"""
    limit = _read_purchase_limit(page)
    result["purchase_limit"] = limit
    if limit is None:
        print(f"[即PAT] 購入限度額を読み取れませんでした({where})")
        return
    # 公開ログに口座の金額を出さない(限度額・合計は出力しない)
    print(f"[即PAT] 購入限度額を確認({where})")
    if limit < total:
        raise IpatError("購入限度額が合計に足りません")


def place_bets(race_id, race_date, horse_stakes, live=False, headless=True, pause=False):
    """1レース分の複勝を即PATで購入する。

    horse_stakes: [(馬番:int, 金額(円、100円単位):int), ...]
    live=False(既定)のドライランでは購入確認画面の金額入力まで進めて
    スクリーンショットを撮り、「購入する」は押さない。
    戻り値: dict(status, total_yen, purchase_limit, screenshot)。
    status: "dry_run" / "purchased"。失敗時はIpatErrorなどの例外。"""
    from playwright.sync_api import sync_playwright

    check_limits(race_id, race_date, horse_stakes)
    parts = common.decode_race_id(race_id)
    venue_name = VENUE_NAMES[parts["place_jra_code"]]
    racenum = parts["racenum"]
    total = sum(int(yen) for _, yen in horse_stakes)
    print(f"[即PAT] {venue_name}{racenum}R 複勝 {len(horse_stakes)}点 live={live}")  # 馬番・金額は公開ログに出さない

    result = {"status": None, "total_yen": total, "purchase_limit": None, "screenshot": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page(locale="ja-JP")
        page.set_default_timeout(STEP_TIMEOUT_MS)
        try:
            _login(page)
            _check_purchase_limit(page, result, total, "メニュー")
            if pause:
                page.pause()

            _select_race(page, venue_name, racenum)
            _enter_place_bets(page, horse_stakes)
            _step(page, "合計金額入力",
                  lambda: page.locator(SELECTORS["total_amount_input"]).first.fill(str(total)))
            if result["purchase_limit"] is None:
                _check_purchase_limit(page, result, total, "購入確認画面")
            result["screenshot"] = _shot(page, race_id, "confirm")
            if pause:
                page.pause()

            if not live:
                print("[即PAT] ドライラン: 購入確認画面で停止しました(購入していません)")
                result["status"] = "dry_run"
            else:
                # 「購入する」を押した後に失敗すると、買えたかどうか分からない。
                # その場合も"uncertain"でログに残し、同じレースの再投票を止める。
                result["status"] = "uncertain"
                _step(page, "購入する", lambda: page.locator(SELECTORS["purchase_button"]).first.click())
                _step(page, "購入確認OK", lambda: page.locator(SELECTORS["confirm_ok"]).first.click())
                # OKを押した時点で購入済み。完了画面待ちの失敗は無視する。
                result["status"] = "purchased"
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                result["screenshot"] = _shot(page, race_id, "done")
                print("[即PAT] 購入しました")
        except Exception:
            _shot(page, race_id, "error")
            if result["status"] == "uncertain":
                print("[即PAT] 購入するを押した後に失敗しました。IPATの投票履歴で購入されたか確認してください。")
                _log_result(race_id, race_date, horse_stakes, live, result["status"])
            raise
        finally:
            browser.close()

    _log_result(race_id, race_date, horse_stakes, live, result["status"])
    return result


def _log_result(race_id, race_date, horse_stakes, live, status):
    now = _dt.now().isoformat()
    _append_purchase_log([
        {"timestamp": now, "date": str(race_date), "race_id_yahoo": str(race_id), "horsenum": int(h),
         "yen": int(y), "live": live, "status": status}
        for h, y in horse_stakes
    ])


def _parse_horse_arg(s):
    horsenum, yen = s.split(":")
    return int(horsenum), int(yen)


def main():
    parser = argparse.ArgumentParser(description="即PATで複勝を投票する(既定はドライラン)")
    parser.add_argument("--race_id", required=True, help="Yahoo形式の10桁race_id")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD、省略時は今日")
    parser.add_argument("--horse", action="append", required=True, type=_parse_horse_arg,
                        help="馬番:金額(円)。複数指定可 例: --horse 3:100 --horse 7:200")
    parser.add_argument("--live", action="store_true", help="実際に購入する(省略時は確認画面で停止)")
    parser.add_argument("--headed", action="store_true", help="ブラウザを表示する")
    parser.add_argument("--pause", action="store_true", help="Playwright Inspectorで途中停止する(セレクタ確認用)")
    args = parser.parse_args()

    race_date = _date.today() if not args.date else _dt.strptime(args.date, "%Y-%m-%d").date()
    res = place_bets(args.race_id, race_date, args.horse, live=args.live,
                     headless=not (args.headed or args.pause), pause=args.pause)
    print(res)


if __name__ == "__main__":
    main()
