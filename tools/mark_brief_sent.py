#!/usr/bin/env python3
"""業界ウォッチ側(Mac)が今朝の速報をLINEに送ったあと、「今日分は送信済み」の印を製造表に書く。
Render側の予備送信(6:40)はこの印を見て、二重送信をやめる。"""
import sys
import warnings
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config_store  # noqa: E402

today = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
config_store.set_config("brief_last_sent", today)
config_store.set_config("brief_last_status", f"{today} 送信済み（業界ウォッチに合流・Macから）")
print("marked", today)
