"""ipat_vote.py が使う最小限の共通処理(元は live_predict/common.py の一部)。"""


def decode_race_id(race_id):
    """Yahoo/netkeibaの10桁race_id(YY+場+回+日+R)を分解する。日付は含まれない。"""
    year2, place, kai, day, racenum = race_id[0:2], race_id[2:4], race_id[4:6], race_id[6:8], race_id[8:10]
    return {"year2": year2, "place_jra_code": int(place), "kai": int(kai), "day": int(day), "racenum": int(racenum)}
