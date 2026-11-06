"""命令列入口。

    python -m monitor run [--date YYYY-MM-DD] [--dsa-report PATH] [--dsa-version v3.33.0]
    python -m monitor dsa-stocks      # 印出給 daily_stock_analysis 的 STOCK_LIST
    python -m monitor site --out _site [--live]   # 建置 GitHub Pages 儀表板
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from .config import load_settings


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m monitor", description="BB、NOK、中光電投控每日監控")
    parser.add_argument("--watchlist", type=Path, default=None, help="觀察清單 YAML（預設 monitor/watchlist.yaml）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="抓資料並產生當日報告")
    run_p.add_argument("--date", type=date.fromisoformat, default=None, help="報告日期（預設今天，依 watchlist 時區）")
    run_p.add_argument("--data-dir", type=Path, default=None)
    run_p.add_argument("--out-dir", type=Path, default=None)
    run_p.add_argument("--dsa-report", type=Path, default=None, help="上游 daily_stock_analysis 產生的 report_*.md")
    run_p.add_argument("--dsa-version", default="未啟用")
    run_p.add_argument("-v", "--verbose", action="store_true")

    sub.add_parser("dsa-stocks", help="印出 daily_stock_analysis 用的 STOCK_LIST")

    site_p = sub.add_parser("site", help="建置靜態 web 儀表板")
    site_p.add_argument("--out", type=Path, default=Path("_site"))
    site_p.add_argument("--data-dir", type=Path, default=None)
    site_p.add_argument("--live", action="store_true", help="另抓盤中報價、最新新聞與櫃買三大法人")

    args = parser.parse_args(argv)
    settings = load_settings(args.watchlist)

    if args.cmd == "dsa-stocks":
        print(",".join(s.dsa_code for s in settings.stocks))
        return 0

    if args.cmd == "site":
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
        from .site import build_site

        out = build_site(settings, args.out, data_dir=args.data_dir, live=args.live)
        print(f"儀表板已建置：{out}")
        return 0

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s %(message)s")
    from .runner import make_context, run

    ctx = make_context(
        settings,
        today=args.date,
        data_dir=args.data_dir,
        out_dir=args.out_dir,
        dsa_report=args.dsa_report,
        dsa_version=args.dsa_version,
    )
    path = run(ctx)
    failed = [e.source for e in ctx.log.entries if not e.ok]
    print(f"報告已寫入：{path}")
    if failed:
        print(f"未取得的來源（{len(failed)}）：{'、'.join(failed)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
