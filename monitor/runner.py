"""每日監控主流程：抓資料 → 預測 → 記錄 → 產生繁中報告。"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import pandas as pd

from . import dsa_bridge, predict, tracking
from .config import Settings, Stock
from .httpclient import FetchLog, HttpClient, SourceResult
from .sources import news as news_src
from .sources import prices, sec_edgar, taiwan, yahoo

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class StockBundle:
    stock: Stock
    prices: SourceResult
    desc: Optional[dict] = None
    prediction: predict.Prediction = field(default_factory=lambda: predict.Prediction(ok=False, reason="未執行"))
    filings: Optional[SourceResult] = None
    thirteen_f: Optional[SourceResult] = None
    yahoo_holders: Optional[SourceResult] = None
    tw_latest: Optional[SourceResult] = None
    tw_finmind: Optional[SourceResult] = None
    tw_history: Optional[pd.DataFrame] = None
    tw_crosscheck: Optional[str] = None
    tw_shareholding: Optional[SourceResult] = None
    tw_margin: Optional[SourceResult] = None
    tdcc: Optional[SourceResult] = None
    tdcc_history: Optional[pd.DataFrame] = None
    news: List[dict] = field(default_factory=list)
    news_sources: List[SourceResult] = field(default_factory=list)
    dsa: Optional[dsa_bridge.DsaSignal] = None
    track: Dict[str, dict] = field(default_factory=dict)


@dataclass
class RunContext:
    settings: Settings
    today: date
    generated_at: datetime
    data_dir: Path
    out_dir: Path
    client: HttpClient
    log: FetchLog = field(default_factory=FetchLog)
    downloader: Optional[Callable[[str, str], pd.DataFrame]] = None
    ticker_factory: Optional[Callable[[str], object]] = None
    dsa_report: Optional[Path] = None
    dsa_version: str = "未啟用"
    dsa_report_rel: Optional[str] = None


def _benchmark(ctx: RunContext, symbol: Optional[str], cache: Dict[str, Optional[pd.Series]]) -> Optional[pd.Series]:
    if not symbol:
        return None
    if symbol not in cache:
        fake = Stock(id=symbol, name=symbol, market="IDX", exchange="", yahoo=symbol, dsa_code=symbol)
        res = prices.fetch_history(fake, ctx.settings.prediction.get("history_period", "5y"), ctx.downloader)
        cache[symbol] = res.data["history"]["close"] if res.ok else None
        if not res.ok:
            ctx.log.add(SourceResult.fail(f"基準指數 {symbol}", res.error or "未取得", res.url, secondary=True))
    return cache[symbol]


def collect_us(ctx: RunContext, b: StockBundle) -> None:
    s = ctx.settings.sec
    st = b.stock
    if st.sec_cik:
        since = ctx.today - timedelta(days=int(s.get("filings_lookback_days", 14)))
        b.filings = ctx.log.add(sec_edgar.fetch_company_filings(ctx.client, st.sec_cik, since))
    if st.cusip:
        b.thirteen_f = ctx.log.add(
            sec_edgar.fetch_13f_positions(
                ctx.client,
                st.cusip,
                ctx.today,
                lookback_days=int(s.get("thirteen_f_lookback_days", 7)),
                max_filings=int(s.get("thirteen_f_max_filings", 40)),
                compare_prior=bool(s.get("thirteen_f_compare_prior", True)),
            )
        )


def collect_tw(ctx: RunContext, b: StockBundle) -> None:
    code = b.stock.tw_code or b.stock.id
    start = taiwan.default_start(ctx.today, 120)
    if b.stock.listed_since:
        start = max(start, date.fromisoformat(b.stock.listed_since))
    b.tw_latest = ctx.log.add(taiwan.fetch_tpex_3insti(ctx.client, code))
    b.tw_finmind = ctx.log.add(taiwan.fetch_finmind_institutional(ctx.client, code, start))
    new_rows = []
    if b.tw_finmind.ok:
        new_rows.extend(b.tw_finmind.data)
    if b.tw_latest.ok:
        new_rows.append(b.tw_latest.data)
    b.tw_history = taiwan.merge_history(
        ctx.data_dir / "tw" / f"{code}_3insti.csv", new_rows, taiwan.INSTI_COLUMNS, prefer_source="TPEx OpenAPI"
    )
    if b.tw_latest.ok:
        b.tw_crosscheck = taiwan.cross_check(b.tw_latest.data, b.tw_finmind.data if b.tw_finmind.ok else [])
    b.tw_shareholding = ctx.log.add(taiwan.fetch_finmind_shareholding(ctx.client, code, start))
    b.tw_margin = ctx.log.add(taiwan.fetch_finmind_margin(ctx.client, code, start))
    b.tdcc = ctx.log.add(taiwan.fetch_tdcc(ctx.client, code))
    b.tdcc_history = taiwan.merge_history(
        ctx.data_dir / "tw" / f"{code}_tdcc.csv",
        [b.tdcc.data] if b.tdcc.ok else [],
        taiwan.TDCC_COLUMNS,
        prefer_source="TDCC 開放資料",
    )


def collect_news(ctx: RunContext, b: StockBundle) -> None:
    lookback = int(ctx.settings.news.get("lookback_days", 7))
    limit = int(ctx.settings.news.get("max_items_per_stock", 8))
    items: List[dict] = []
    if b.stock.news_queries:
        res = ctx.log.add(news_src.fetch_google_news(ctx.client, b.stock.news_queries, lookback))
        b.news_sources.append(res)
        if res.ok:
            items.extend(res.data)
    if b.stock.is_us:
        res = ctx.log.add(yahoo.fetch_news(b.stock, ctx.ticker_factory))
        b.news_sources.append(res)
        if res.ok:
            items.extend(res.data)
    b.news = news_src.dedupe_and_trim(items, news_src.since_utc(ctx.generated_at, lookback), limit)


def run(ctx: RunContext) -> Path:
    settings = ctx.settings
    pcfg = settings.prediction
    horizon = int(pcfg.get("horizon_days", 5))
    bench_cache: Dict[str, Optional[pd.Series]] = {}
    bundles: List[StockBundle] = []

    dsa_signals: Dict[str, dsa_bridge.DsaSignal] = {}
    if ctx.dsa_report and ctx.dsa_report.exists():
        rel = f"{ctx.today:%Y}/{ctx.today:%Y-%m-%d}-llm.md"
        body = dsa_bridge.convert_report(ctx.dsa_report, ctx.out_dir / rel, ctx.dsa_version, f"{ctx.today:%Y-%m-%d}")
        ctx.dsa_report_rel = rel
        dsa_signals = dsa_bridge.match_codes(
            dsa_bridge.parse_summary(body), {s.id: s.dsa_code for s in settings.stocks}
        )
        ctx.log.add(
            SourceResult(
                source="daily_stock_analysis（LLM 意見）",
                ok=True,
                note=f"擷取 {len(dsa_signals)} 檔結論；LLM 生成內容，未經核實",
                url=dsa_bridge.DSA_REPO,
                secondary=True,
            )
        )
    elif ctx.dsa_report:
        ctx.log.add(
            SourceResult.fail("daily_stock_analysis（LLM 意見）", "本次未產生報告檔", dsa_bridge.DSA_REPO, secondary=True)
        )

    log_rows: List[dict] = []
    histories: Dict[str, pd.DataFrame] = {}
    for st in settings.stocks:
        logger.info("處理 %s", st.id)
        price_res = ctx.log.add(prices.fetch_history(st, pcfg.get("history_period", "5y"), ctx.downloader))
        b = StockBundle(stock=st, prices=price_res)
        if price_res.ok:
            hist = price_res.data["history"]
            histories[st.id] = hist
            b.desc = prices.describe(hist[hist.index >= pd.Timestamp(st.listed_since)] if st.listed_since else hist)
            b.prediction = predict.walk_forward(
                hist,
                _benchmark(ctx, st.benchmark, bench_cache),
                horizon=horizon,
                min_train_rows=int(pcfg.get("min_train_rows", 250)),
                retrain_every=int(pcfg.get("retrain_every", 21)),
                min_oos_samples=int(pcfg.get("min_oos_samples", 120)),
            )
        else:
            b.prediction = predict.Prediction(ok=False, reason="價格資料未取得", horizon=horizon)

        if st.is_us:
            collect_us(ctx, b)
        if st.is_tw:
            collect_tw(ctx, b)
        b.yahoo_holders = ctx.log.add(yahoo.fetch_holders(st, ctx.ticker_factory)) if st.is_us else None
        collect_news(ctx, b)
        b.dsa = dsa_signals.get(st.id)

        asof = b.desc["last_date"] if b.desc else None
        if b.prediction.ok and asof is not None:
            log_rows.append(
                {
                    "run_date": f"{ctx.today:%Y-%m-%d}",
                    "stock_id": st.id,
                    "model": predict.MODEL_NAME,
                    "asof_date": f"{b.prediction.asof:%Y-%m-%d}",
                    "horizon": horizon,
                    "prob_up": round(b.prediction.prob_up, 4),
                    "direction": predict.direction_of(b.prediction),
                    "close_asof": b.desc["last_close"],
                    "beats_baseline": b.prediction.beats_baseline,
                    "label": b.prediction.signal,
                }
            )
        if b.dsa and asof is not None:
            log_rows.append(
                {
                    "run_date": f"{ctx.today:%Y-%m-%d}",
                    "stock_id": st.id,
                    "model": "dsa_llm",
                    "asof_date": f"{asof:%Y-%m-%d}",
                    "horizon": horizon,
                    "prob_up": None,
                    "direction": tracking.llm_direction(b.dsa.trend),
                    "close_asof": b.desc["last_close"],
                    "beats_baseline": None,
                    "label": f"{b.dsa.signal}／{b.dsa.trend}",
                }
            )
        bundles.append(b)

    log = tracking.append(ctx.data_dir / "predictions.csv", log_rows)
    stats = tracking.evaluate(log, histories)
    for b in bundles:
        b.track = {model: v for (sid, model), v in stats.items() if sid == b.stock.id}

    from . import report

    text = report.render(ctx, bundles)
    path = ctx.out_dir / f"{ctx.today:%Y}" / f"{ctx.today:%Y-%m-%d}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    report.update_index(ctx.out_dir, ctx.today, settings)

    from . import export

    export.write_json(ctx.data_dir / "dashboard.json", export.build_dashboard(ctx, bundles, path))
    return path


def make_context(
    settings: Settings,
    today: Optional[date] = None,
    data_dir: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    dsa_report: Optional[Path] = None,
    dsa_version: str = "未啟用",
    client: Optional[HttpClient] = None,
) -> RunContext:
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    return RunContext(
        settings=settings,
        today=today or now.date(),
        generated_at=now,
        data_dir=data_dir or REPO_ROOT / "monitor" / "data",
        out_dir=out_dir or REPO_ROOT / "monitor" / "reports",
        client=client or HttpClient(),
        dsa_report=dsa_report,
        dsa_version=dsa_version,
    )
