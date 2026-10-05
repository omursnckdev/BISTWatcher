"""Command-line interface.

Examples (English command names work too: scan, analyze, regime, ...)::

    python -m bist_quant tara                        # configured universe (BIST30)
    python -m bist_quant tara --evren BIST100 --ilk 20
    python -m bist_quant tara --semboller THYAO ASELS --detay
    python -m bist_quant tara --kaynak synthetic     # offline demo data
    python -m bist_quant tara --tarih 2025-06-30     # replay using data <= that date
    python -m bist_quant tara --alim-esigi 60        # looser BUY thresholds for one run
    python -m bist_quant analiz THYAO
    python -m bist_quant piyasa
    python -m bist_quant evren --evren BIST50

    python -m bist_quant geritest --evren BIST30 --baslangic 2018-01-01
    python -m bist_quant geritest --ayarla risk.use_resistance_cap=false --islemler trades.csv
    python -m bist_quant parametre-tara --izgara strategy.buy_threshold_offset=[-10,-5,0,5]
    python -m bist_quant ileri-test
    python -m bist_quant arastirma
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from bist_quant import __version__
from bist_quant.config import Settings, apply_overrides, load_settings, parse_override
from bist_quant.data.market_data import (
    ISTANBUL_TZ,
    CachingProvider,
    MarketDataProvider,
    build_provider,
)
from bist_quant.data.universe import load_universe, normalize_symbol
from bist_quant.logging import configure_logging
from bist_quant.models.signals import MarketRegime, SignalType
from bist_quant.reporting import (
    SIGNAL_RANK,
    format_detail,
    format_header,
    format_table,
    save_outputs,
    to_json,
)
from bist_quant.scanner import ScanError, ScanResult, run_scan

# Turkish command names; the English names keep working as aliases.
COMMANDS = {
    "tara": "scan",
    "analiz": "analyze",
    "piyasa": "regime",
    "evren": "universe",
    "geritest": "backtest",
    "parametre-tara": "sweep",
    "ileri-test": "walkforward",
    "arastirma": "research",
    "kap-indir": "kap-sync",
    "haber": "news",
}
_CANONICAL = {**COMMANDS, **{en: en for en in COMMANDS.values()}}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="bist_quant",
        description="Açıklanabilir BIST swing tarayıcısı ve araştırma aracı. "
        "Komut ve seçeneklerin İngilizce adları da çalışır.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument(
        "--ayar-dizini",
        "--config-dir",
        dest="config_dir",
        type=Path,
        metavar="DİZİN",
        help="YAML ayar dosyalarının bulunduğu dizin",
    )
    p.add_argument(
        "-v",
        "--ayrintili",
        "--verbose",
        dest="verbose",
        action="store_true",
        help="stderr'e yapılandırılmış INFO logları yaz",
    )
    sub = p.add_subparsers(dest="command", required=True, metavar="KOMUT")

    def cmd(name: str, help_text: str) -> argparse.ArgumentParser:
        return sub.add_parser(name, aliases=[COMMANDS[name]], help=help_text)

    def universe_arg(sp: argparse.ArgumentParser, help_text: str) -> None:
        sp.add_argument("--evren", "--universe", dest="universe", metavar="EVREN", help=help_text)

    def symbols_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--semboller",
            "--symbols",
            dest="symbols",
            nargs="+",
            metavar="SEMBOL",
            help="belirli semboller, örn. THYAO ASELS (--evren yerine geçer)",
        )

    def provider_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--kaynak",
            "--provider",
            dest="provider",
            choices=["yahoo", "csv", "synthetic"],
            help="veri kaynağı: yahoo (gerçek), csv (kendi dosyaların), synthetic (demo)",
        )
        sp.add_argument(
            "--yenile",
            "--refresh",
            dest="refresh",
            action="store_true",
            help="önbelleği yok say, veriyi yeniden indir",
        )

    def no_news_arg(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--habersiz",
            "--no-news",
            dest="no_news",
            action="store_true",
            help="KAP haber faktörünü kapat",
        )

    def data_args(sp: argparse.ArgumentParser) -> None:
        provider_args(sp)
        sp.add_argument(
            "--tarih",
            "--as-of",
            dest="as_of",
            type=date.fromisoformat,
            metavar="YYYY-AA-GG",
            help="geçmiş bir seansa göre değerlendir; o tarihten sonraki veri kullanılmaz",
        )
        no_news_arg(sp)
        sp.add_argument(
            "--alim-esigi",
            "--buy-threshold",
            dest="buy_threshold",
            type=float,
            metavar="PUAN",
            help="BOĞA piyasası AL eşiği; diğer piyasa durumlarının eşikleri aynı miktarda kayar",
        )

    scan = cmd("tara", "evreni tara ve sıralı tabloyu yazdır")
    data_args(scan)
    universe_arg(scan, "BIST30 | BIST50 | BIST100 | CUSTOM | <liste adı>")
    symbols_arg(scan)
    scan.add_argument(
        "--ilk", "--top", dest="top", type=int, metavar="N", help="yalnızca ilk N satırı göster"
    )
    scan.add_argument(
        "--en-az-sinyal",
        "--min-signal",
        dest="min_signal",
        choices=[s.value for s in SignalType],
        help="bu sinyal seviyesinin altındaki satırları gizle",
    )
    scan.add_argument(
        "--detay",
        "--detail",
        dest="detail",
        action="store_true",
        help="her hisse için ayrıntılı dökümü yazdır",
    )
    scan.add_argument("--json", type=Path, metavar="DOSYA", help="JSON olarak yaz ('-' = ekrana)")
    scan.add_argument(
        "--kaydet",
        "--save",
        dest="save",
        action="store_true",
        help="sinyalleri ve günlük göstergeleri data/processed/ altına CSV olarak yaz",
    )

    analyze = cmd("analiz", "hisseler için ayrıntılı, gerekçeli analiz")
    data_args(analyze)
    analyze.add_argument("symbols", nargs="+", metavar="SEMBOL")

    regime = cmd("piyasa", "güncel piyasa durumunu göster")
    data_args(regime)
    universe_arg(regime, "piyasa genişliği ölçümünde kullanılacak evren")

    uni = cmd("evren", "bir evrendeki sembolleri listele")
    universe_arg(uni, "evren adı, örn. BIST50")

    def bt_args(sp: argparse.ArgumentParser) -> None:
        provider_args(sp)
        universe_arg(sp, "varsayılan: backtest.yaml içindeki backtest.universe")
        symbols_arg(sp)
        sp.add_argument(
            "--baslangic",
            "--start",
            dest="start",
            type=date.fromisoformat,
            metavar="YYYY-AA-GG",
            help="başlangıç tarihi",
        )
        sp.add_argument(
            "--bitis",
            "--end",
            dest="end",
            type=date.fromisoformat,
            metavar="YYYY-AA-GG",
            help="bitiş tarihi",
        )
        sp.add_argument(
            "--ayarla",
            "--set",
            dest="set",
            action="append",
            default=[],
            metavar="ANAHTAR=DEĞER",
            help="bir ayarı değiştir, örn. --ayarla risk.minimum_rr=2.5 (tekrarlanabilir)",
        )
        no_news_arg(sp)

    bt = cmd("geritest", "maliyetler ve karşılaştırma endeksleriyle geçmiş simülasyonu")
    bt_args(bt)
    bt.add_argument(
        "--islemler",
        "--trades",
        dest="trades",
        type=Path,
        metavar="CSV",
        help="işlem listesini CSV'ye yaz",
    )
    bt.add_argument(
        "--ozkaynak",
        "--equity",
        dest="equity",
        type=Path,
        metavar="CSV",
        help="günlük özkaynak ve endeks eğrilerini CSV'ye yaz",
    )

    sw = cmd("parametre-tara", "aynı dönemde parametre kombinasyonlarını karşılaştır")
    bt_args(sw)
    sw.add_argument(
        "--izgara",
        "--grid",
        dest="grid",
        action="append",
        default=[],
        metavar="ANAHTAR=[D1,D2]",
        help="denenecek değerler (varsayılan: walk_forward.grid)",
    )
    sw.add_argument("--csv", type=Path, help="sonuç tablosunu CSV'ye yaz")

    wf = cmd("ileri-test", "ileriye dönük (walk-forward) parametre seçimi ve örneklem dışı test")
    bt_args(wf)

    rs = cmd("arastirma", "puan aralığı, sinyal ve piyasa durumuna göre ileri getiriler")
    bt_args(rs)

    ks = cmd("kap-indir", "bir evren ve dönem için KAP bildirimlerini indir")
    universe_arg(ks, "evren adı")
    symbols_arg(ks)
    ks.add_argument(
        "--baslangic", "--start", dest="start", type=date.fromisoformat, metavar="YYYY-AA-GG"
    )
    ks.add_argument("--bitis", "--end", dest="end", type=date.fromisoformat, metavar="YYYY-AA-GG")
    ks.add_argument(
        "--metinler",
        "--details",
        dest="details",
        action="store_true",
        help="tutar içeren bildirim türlerinin metinlerini de indir",
    )

    nw = cmd("haber", "sınıflandırılmış KAP olayları ve güncel haber puanı")
    nw.add_argument("symbols", nargs="+", metavar="SEMBOL")
    nw.add_argument(
        "--gun",
        "--days",
        dest="days",
        type=int,
        default=30,
        help="kaç gün geriye bakılsın (varsayılan 30)",
    )
    nw.add_argument(
        "--esitleme-yok",
        "--no-sync",
        dest="no_sync",
        action="store_true",
        help="yalnızca önbellekteki KAP verisini kullan",
    )
    return p


def _apply_buy_threshold(settings: Settings, args: argparse.Namespace) -> Settings:
    """--alim-esigi sets the BULL threshold; every regime shifts by the same amount."""
    value = getattr(args, "buy_threshold", None)
    if value is None:
        return settings
    bull = settings.strategy.buy_threshold[MarketRegime.BULL]
    return apply_overrides(settings, {"strategy.buy_threshold_offset": value - bull})


def _news_active(settings: Settings, args: argparse.Namespace) -> bool:
    """News needs real tickers: off for synthetic data or when --no-news is given."""
    provider = getattr(args, "provider", None) or settings.data.provider
    if getattr(args, "no_news", False) or provider == "synthetic":
        settings.news.enabled = False
    return settings.news.enabled


async def _news_events(settings: Settings, symbols: list[str], start: date, end: date):
    from bist_quant.news.pipeline import load_news_events

    try:
        return await load_news_events(settings, symbols, start, end)
    except Exception as exc:  # noqa: BLE001 - news must never block a scan
        print(f"Warning: KAP news unavailable ({exc}); news factor disabled.", file=sys.stderr)
        return None


def _provider(settings: Settings, args: argparse.Namespace) -> MarketDataProvider:
    if getattr(args, "provider", None):
        settings.data.provider = args.provider
    provider = build_provider(settings)
    if isinstance(provider, CachingProvider) and getattr(args, "refresh", False):
        provider.force_refresh = True
    return provider


async def _scan(
    settings: Settings, symbols: list[str], breadth: list[str], args: argparse.Namespace
) -> ScanResult:
    provider = _provider(settings, args)
    events = None
    if symbols and _news_active(settings, args):
        from bist_quant.news.pipeline import news_window

        end = args.as_of or datetime.now(ISTANBUL_TZ).date()
        events = await _news_events(settings, symbols, *news_window(end, settings))
    try:
        return await run_scan(
            settings,
            symbols,
            provider,
            as_of=args.as_of,
            breadth_symbols=breadth,
            news_events=events,
        )
    finally:
        inner = getattr(provider, "inner", provider)
        if hasattr(inner, "aclose"):
            await inner.aclose()


async def _history(settings: Settings, symbols: list[str], args: argparse.Namespace):
    from bist_quant.backtest.data import load_history

    provider = _provider(settings, args)
    try:
        return await load_history(settings, symbols, provider, args.start, args.end)
    finally:
        inner = getattr(provider, "inner", provider)
        if hasattr(inner, "aclose"):
            await inner.aclose()


def _research_commands(settings: Settings, args: argparse.Namespace) -> int:
    from bist_quant.backtest.report import (
        format_backtest,
        format_event_study,
        format_research,
        format_sweep,
        format_walk_forward,
    )
    from bist_quant.backtest.research import run_event_study, run_research
    from bist_quant.backtest.runner import BacktestContext, sweep
    from bist_quant.backtest.walk_forward import walk_forward

    if args.set:
        settings = apply_overrides(settings, dict(parse_override(x) for x in args.set))
    universe_name = (args.universe or settings.backtest.universe).upper()
    if args.symbols:
        symbols = [normalize_symbol(s) for s in args.symbols]
        universe_name = "CUSTOM"
    else:
        symbols = load_universe(settings.universe, universe_name)
    print(f"Loading {len(symbols)} symbols + benchmarks ...", file=sys.stderr)
    history = asyncio.run(_history(settings, symbols, args))
    events = None
    if _news_active(settings, args):
        print("Loading KAP disclosures (first run downloads history) ...", file=sys.stderr)
        start = history.start.date() - timedelta(days=int(settings.news.decay.max_age_days) + 3)
        events = asyncio.run(_news_events(settings, symbols, start, history.end.date()))
    ctx = BacktestContext(history, events)
    print("Scoring history ...", file=sys.stderr)
    initial = settings.backtest.initial_equity

    if args.command == "backtest":
        result = ctx.run(settings)
        if not len(result.equity):
            print("No sessions to simulate in the requested period.", file=sys.stderr)
            return 1
        benches = ctx.benchmarks(result.equity.index, initial)
        print(format_backtest(result, benches, universe_name, len(history.bars)))
        if history.skipped:
            print("Skipped: " + "; ".join(f"{k} ({v})" for k, v in history.skipped.items()))
        if args.trades:
            result.trades_frame().to_csv(args.trades, index=False)
            print(f"Trades written to {args.trades}")
        if args.equity:
            import pandas as pd

            pd.DataFrame({"strategy": result.equity, **benches}).to_csv(args.equity)
            print(f"Equity curves written to {args.equity}")
        return 0

    if args.command == "sweep":
        grid = (
            dict(parse_override(x) for x in args.grid) if args.grid else settings.walk_forward.grid
        )
        for key, values in grid.items():
            if not isinstance(values, list):
                grid[key] = [values]
        table = sweep(ctx, settings, grid)
        print(format_sweep(table, list(grid)))
        if args.csv:
            table.to_csv(args.csv, index=False)
        return 0

    if args.command == "walkforward":
        result = walk_forward(ctx, settings)
        if not result.folds:
            print("Period too short for the configured train/test windows.", file=sys.stderr)
            return 1
        benches = ctx.benchmarks(result.oos_equity.index, initial)
        print(format_walk_forward(result, benches))
        return 0

    research = run_research(ctx, settings)
    print(format_research(research, settings.research.horizons))
    study = run_event_study(ctx, settings)
    if study is not None:
        print()
        print(format_event_study(study, settings.research.horizons))
    return 0


def _kap_sync(settings: Settings, args: argparse.Namespace) -> int:
    from bist_quant.data.kap_data import KapClient, KapStore, sync_disclosures

    symbols = (
        [normalize_symbol(s) for s in args.symbols]
        if args.symbols
        else load_universe(settings.universe, args.universe)
    )
    end = args.end or datetime.now(ISTANBUL_TZ).date()
    start = args.start or (settings.backtest.start - timedelta(days=30))

    async def run() -> dict:
        client = KapClient()
        try:
            store = KapStore(settings.resolve_path(settings.news.kap_dir))
            return await sync_disclosures(client, store, symbols, start, end)
        finally:
            await client.aclose()

    stats = asyncio.run(run())
    print(f"KAP sync {start} -> {end} for {len(symbols)} symbols: {stats}")
    if args.details:
        from bist_quant.config import apply_overrides
        from bist_quant.news.pipeline import load_news_events

        big = apply_overrides(settings, {"news.detail_max_per_run": 1_000_000, "news.sync": False})
        events = asyncio.run(load_news_events(big, symbols, start, end))
        print(f"Disclosure texts up to date ({sum(len(v) for v in events.values())} events).")
    return 0


def _news_command(settings: Settings, args: argparse.Namespace) -> int:
    from bist_quant.backtest.data import load_history
    from bist_quant.indicators.features import compute_features
    from bist_quant.news.pipeline import attach_reactions, load_news_events

    symbols = [normalize_symbol(s) for s in args.symbols]
    today = datetime.now(ISTANBUL_TZ).date()
    start = today - timedelta(days=args.days)

    async def run():
        events = await load_news_events(settings, symbols, start, today, sync=not args.no_sync)
        provider = build_provider(settings)
        try:
            hist = await load_history(settings, symbols, provider, start, today)
        finally:
            inner = getattr(provider, "inner", provider)
            if hasattr(inner, "aclose"):
                await inner.aclose()
        return events, hist

    events, hist = asyncio.run(run())
    feats = {
        s: compute_features(
            b,
            settings.indicators,
            settings.scoring.rules,
            settings.risk,
            benchmark_close=hist.index_bars["close"],
        )
        for s, b in hist.bars.items()
    }
    book = attach_reactions(events, feats, hist.index_bars["close"], settings)
    now = datetime.now(ISTANBUL_TZ)
    weight = settings.scoring.weights.news
    for sym in symbols:
        comp = book.component(sym, now, weight)
        print(
            f"\n{sym}   news score {comp.points:.1f}/{weight:.0f}   "
            f"net impact {book.net_impact(sym, now):+.2f}"
        )
        rows = [e for e in book.events.get(sym, [])]
        if not rows:
            print("  no classified KAP events in the window")
        for e in sorted(rows, key=lambda e: e.article.published_at, reverse=True):
            c = e.classification
            extra = f"  size x{e.scale_multiplier:.2f}" if e.scale_multiplier != 1 else ""
            if e.reaction:
                extra += f"  [{e.reaction}]"
            print(
                f"  {e.article.published_at:%Y-%m-%d %H:%M}  {c.event_type.value:<20} "
                f"sent {c.sentiment:+.1f} imp {c.importance:.1f}  "
                f"{(e.article.summary or e.article.subject)[:60]}{extra}"
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.command = _CANONICAL[args.command]
    configure_logging("INFO" if args.verbose else None)
    try:
        settings = load_settings(args.config_dir)
    except (ValueError, OSError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    try:
        settings = _apply_buy_threshold(settings, args)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    if args.command == "kap-sync":
        return _kap_sync(settings, args)
    if args.command == "news":
        return _news_command(settings, args)

    if args.command in {"backtest", "sweep", "walkforward", "research"}:
        try:
            return _research_commands(settings, args)
        except ScanError as exc:
            print(f"Backtest failed: {exc}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2

    try:
        if args.command == "universe":
            for sym in load_universe(settings.universe, args.universe):
                print(sym)
            return 0

        universe = load_universe(settings.universe, getattr(args, "universe", None))
        if getattr(args, "symbols", None):
            symbols = [normalize_symbol(s) for s in args.symbols]
        elif args.command == "regime":
            symbols = []
        else:
            symbols = universe
        # Market breadth is always measured on the configured universe.
        result = asyncio.run(_scan(settings, symbols, universe, args))
    except ScanError as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    if args.command == "regime":
        print(format_header(result))
        return 0

    if args.command == "analyze":
        print(format_header(result))
        for s in result.signals:
            print("\n" + "=" * 60 + "\n" + format_detail(s))
        for sym, why in result.skipped.items():
            print(f"\n{sym}: skipped ({why})")
        return 0 if result.signals else 1

    if args.min_signal:
        floor = SIGNAL_RANK[SignalType(args.min_signal)]
        result.signals = [s for s in result.signals if SIGNAL_RANK[s.signal] >= floor]
    if args.json and str(args.json) == "-":
        print(to_json(result))
        return 0
    print(format_table(result, args.top))
    if args.detail:
        for s in result.signals[: args.top] if args.top else result.signals:
            print("\n" + "=" * 60 + "\n" + format_detail(s))
    if args.json:
        args.json.write_text(to_json(result), encoding="utf-8")
        print(f"\nJSON written to {args.json}")
    if args.save:
        for path in save_outputs(result, settings.resolve_path(Path("data/processed"))):
            print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
