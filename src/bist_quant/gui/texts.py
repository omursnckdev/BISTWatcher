"""Turkish labels for the desktop app.

The engine writes its explanations in English (they are also used by the CLI and
the JSON output). :func:`tr` translates the known phrases; anything it does not
recognise is shown unchanged rather than hidden.
"""

from __future__ import annotations

import re

from bist_quant.models.signals import MarketRegime, SignalType
from bist_quant.strategy.exit_check import ExitAction

SIGNAL_TR = {
    SignalType.STRONG_BUY_CANDIDATE: "GÜÇLÜ AL",
    SignalType.BUY_CANDIDATE: "AL",
    SignalType.WEAK_SETUP: "ZAYIF KURULUM",
    SignalType.WATCH: "İZLE",
    SignalType.NO_TRADE: "İŞLEM YOK",
}

# Background colours for signal cells (light, readable with black text).
SIGNAL_COLOR = {
    SignalType.STRONG_BUY_CANDIDATE: "#57bb6e",
    SignalType.BUY_CANDIDATE: "#a5d6a7",
    SignalType.WEAK_SETUP: "#ffe082",
    SignalType.WATCH: "#bbdefb",
    SignalType.NO_TRADE: "#eeeeee",
}
SELL_COLOR = "#ef9a9a"
PARTIAL_COLOR = "#ffcc80"
HOLD_COLOR = "#c8e6c9"

REGIME_TR = {
    MarketRegime.BULL: "BOĞA",
    MarketRegime.NEUTRAL: "NÖTR",
    MarketRegime.BEAR: "AYI",
    MarketRegime.HIGH_VOLATILITY: "YÜKSEK OYNAKLIK",
}

COMPONENT_TR = {
    "trend": "Trend",
    "momentum": "Momentum",
    "volume": "Hacim",
    "relative_strength": "Göreceli Güç",
    "news": "Haber / KAP",
    "institutional_flow": "Kurumsal Akış",
    "market_regime": "Piyasa Durumu",
    "volatility": "Oynaklık / Kurulum",
}
COMPONENT_SHORT = {
    "trend": "Trend",
    "momentum": "Mom.",
    "volume": "Hacim",
    "relative_strength": "RG",
    "news": "Haber",
    "market_regime": "Piyasa",
    "volatility": "Oynak.",
}

ACTION_TR = {
    ExitAction.HOLD: "TUT",
    ExitAction.TAKE_PARTIAL: "KISMİ SAT",
    ExitAction.SELL: "SAT",
}
ACTION_COLOR = {
    ExitAction.HOLD: HOLD_COLOR,
    ExitAction.TAKE_PARTIAL: PARTIAL_COLOR,
    ExitAction.SELL: SELL_COLOR,
}

EXIT_REASON_TR = {
    "stop": "Zarar-durdur (stop) seviyesine değdi",
    "breakeven_stop": "TP1 sonrası maliyete çekilen stop'a değdi",
    "trailing_stop": "İz süren stop'a değdi",
    "tp1": "1. hedef (TP1) görüldü: pozisyonun bir kısmını sat",
    "tp2": "2. hedef (TP2) görüldü: kalanını sat",
    "trend_exit": "Trend bozuldu: kapanış EMA20 altında ve MACD sinyal çizgisinin altında",
    "time_stop": "Zaman stop'u: beklenen hareket gelmedi",
    "max_hold": "Azami tutma süresi doldu (swing ufku)",
    "no_data": "Veri yok veya risk hesaplanamadı",
    "before_entry": "Alış tarihi elimizdeki verinin dışında",
}

_PATTERNS: list[tuple[str, str]] = [
    # trend
    (r"^Price above EMA(\d+)$", r"Fiyat EMA\1 üzerinde"),
    (r"^Price below EMA(\d+)$", r"Fiyat EMA\1 altında"),
    (r"^EMA(\d+) above EMA(\d+)$", r"EMA\1, EMA\2 üzerinde"),
    (r"^EMA(\d+) below EMA(\d+)$", r"EMA\1, EMA\2 altında"),
    (
        r"^ADX (\d+) with -DI dominant \(directional pressure is down\)$",
        r"ADX \1, -DI baskın (yön aşağı)",
    ),
    (r"^Strong uptrend: ADX (\d+), \+DI > -DI$", r"Güçlü yükseliş trendi: ADX \1, +DI > -DI"),
    (r"^Emerging uptrend: ADX (\d+)$", r"Yeni oluşan yükseliş trendi: ADX \1"),
    (r"^Weak trend / sideways: ADX (\d+)$", r"Zayıf trend / yatay: ADX \1"),
    (
        r"^Closed above EMA(\d+) on (\d+)% of last (\d+) sessions$",
        r"Son \3 seansın %\2'sinde EMA\1 üzerinde kapattı",
    ),
    # momentum
    (r"^RSI healthy at (\d+)$", r"RSI sağlıklı: \1"),
    (r"^RSI strong at (\d+)$", r"RSI güçlü: \1"),
    (r"^RSI (\d+): potentially overextended$", r"RSI \1: aşırı uzamış olabilir"),
    (r"^RSI weak at (\d+)$", r"RSI zayıf: \1"),
    (
        r"^RSI oversold at (\d+) \(no trend confirmation\)$",
        r"RSI aşırı satımda: \1 (trend teyidi yok)",
    ),
    (r"^Positive rate of change \((.+)\)$", r"Pozitif değişim oranı (\1)"),
    (r"^Negative rate of change \((.+)\)$", r"Negatif değişim oranı (\1)"),
    (
        r"^RSI above 50 on (\d+)% of last (\d+) sessions$",
        r"RSI son \2 seansın %\1'inde 50 üzerinde",
    ),
    (
        r"^RSI above 50 on only (\d+)% of last (\d+) sessions$",
        r"RSI son \2 seansın yalnızca %\1'inde 50 üzerinde",
    ),
    (r"^MACD above signal line$", "MACD sinyal çizgisinin üzerinde"),
    (r"^MACD below signal line$", "MACD sinyal çizgisinin altında"),
    (r"^MACD histogram rising$", "MACD histogramı yükseliyor"),
    (r"^MACD histogram falling$", "MACD histogramı düşüyor"),
    (r"^MACD histogram crossed above zero$", "MACD histogramı sıfırın üzerine geçti"),
    (r"^MACD histogram positive$", "MACD histogramı pozitif"),
    (r"^MACD histogram negative$", "MACD histogramı negatif"),
    (r"^Rate of change$", "Değişim oranı"),
    (r"^Volume ratio unavailable$", "Hacim oranı hesaplanamadı"),
    (
        r"^Bollinger breakout without volume confirmation$",
        "Hacim teyidi olmadan Bollinger kırılımı",
    ),
    (r"^Extension unavailable$", "EMA20'ye uzaklık hesaplanamadı"),
    (
        r"^Close above upper Bollinger Band on above-average volume$",
        "Ortalama üstü hacimle üst Bollinger bandının üzerinde kapanış",
    ),
    # volume
    (r"^Up day on ([\d.]+)x average volume$", r"Ortalamanın \1 katı hacimle yükseliş"),
    (r"^Up day on unremarkable volume \(([\d.]+)x\)$", r"Sıradan hacimle yükseliş (\1x)"),
    (r"^Up day on thin volume \(([\d.]+)x\)$", r"Zayıf hacimle yükseliş (\1x)"),
    (
        r"^Down day on ([\d.]+)x average volume \(distribution risk\)$",
        r"Ortalamanın \1 katı hacimle düşüş (dağıtım riski)",
    ),
    (r"^Pullback on light volume \(([\d.]+)x\)$", r"Düşük hacimli geri çekilme (\1x)"),
    (r"^Down day on ([\d.]+)x average volume$", r"Ortalamanın \1 katı hacimle düşüş"),
    (
        r"^Accumulation/Distribution line rising over (\d+) sessions$",
        r"Birikim/Dağıtım çizgisi son \1 seansta yükseliyor",
    ),
    (
        r"^Accumulation/Distribution line falling over (\d+) sessions$",
        r"Birikim/Dağıtım çizgisi son \1 seansta düşüyor",
    ),
    (r"^OBV above its average \(accumulation\)$", "OBV ortalamasının üzerinde (birikim)"),
    (r"^OBV below its average$", "OBV ortalamasının altında"),
    # relative strength
    (r"^(\d+)D relative strength unavailable$", r"\1 günlük göreceli güç hesaplanamadı"),
    (
        r"^Outperforming: (\d+)D return ([+-][\d.]+)pp vs (\w+)$",
        r"Endeksten iyi: \1 günlük getiri \3'e göre \2 puan",
    ),
    (
        r"^Lagging: (\d+)D return ([+-][\d.]+)pp vs (\w+)$",
        r"Endeksin gerisinde: \1 günlük getiri \3'e göre \2 puan",
    ),
    # volatility / setup
    (
        r"^ATR ([\d.]+)% of price \(tradable volatility\)$",
        r"ATR fiyatın %\1'i (işlem yapılabilir oynaklık)",
    ),
    (
        r"^ATR only ([\d.]+)% of price \(low reward potential\)$",
        r"ATR fiyatın yalnızca %\1'i (düşük getiri potansiyeli)",
    ),
    (r"^ATR ([\d.]+)% of price \(very volatile\)$", r"ATR fiyatın %\1'i (çok oynak)"),
    (
        r"^Not overextended \(([\d.]+) ATR above EMA(\d+)\)$",
        r"Aşırı uzamamış (EMA\2'nin \1 ATR üzerinde)",
    ),
    (r"^Shallow pullback to EMA(\d+) \((-?[\d.]+) ATR\)$", r"EMA\1'e sığ geri çekilme (\2 ATR)"),
    (r"^Overextended: ([\d.]+) ATR above EMA(\d+)$", r"Aşırı uzamış: EMA\2'nin \1 ATR üzerinde"),
    (r"^([\d.]+) ATR below EMA(\d+)$", r"EMA\2'nin \1 ATR altında"),
    (
        r"^Bollinger squeeze: band width in bottom (\d+)% of (\d+)D range$",
        r"Bollinger sıkışması: bant genişliği \2 günlük aralığın en alt %\1'inde",
    ),
    # market regime
    (r"^Index above EMA(\d+)$", r"Endeks EMA\1 üzerinde"),
    (r"^Index below EMA(\d+)$", r"Endeks EMA\1 altında"),
    (r"^Index EMA50 above EMA200$", "Endeks EMA50, EMA200 üzerinde"),
    (r"^Index EMA50 below EMA200$", "Endeks EMA50, EMA200 altında"),
    (r"^Index MACD positive$", "Endeks MACD pozitif"),
    (r"^Index MACD negative$", "Endeks MACD negatif"),
    (
        r"^Breadth healthy: (\d+)% of universe above EMA50$",
        r"Piyasa genişliği sağlıklı: hisselerin %\1'i EMA50 üzerinde",
    ),
    (
        r"^Weak breadth: (\d+)% of universe above EMA50$",
        r"Piyasa genişliği zayıf: hisselerin yalnızca %\1'i EMA50 üzerinde",
    ),
    (
        r"^index below EMA50 and EMA50 below EMA200$",
        "endeks EMA50 altında, EMA50 de EMA200 altında",
    ),
    (
        r"^index ATR% at (\d+)% percentile of (\d+)D range \(([\d.]+)x median\)$",
        r"endeks ATR%'si \2 günlük aralığın %\1 diliminde (medyanın \3 katı)",
    ),
    (r"^index ([\d.]+)% below its (\d+)D high$", r"endeks \2 günlük zirvesinin %\1 altında"),
    (
        r"^index above EMA50, EMA50 above EMA200, MACD positive$",
        "endeks EMA50 üzerinde, EMA50 EMA200 üzerinde, MACD pozitif",
    ),
    (r"^mixed index trend signals$", "endeks trend sinyalleri karışık"),
    # filters / signal notes
    (
        r"^Illiquid: 20D avg turnover (.+) TRY < (.+)$",
        r"Likidite yetersiz: 20 günlük ort. işlem hacmi \1 TL < \2 TL",
    ),
    (
        r"^Illiquid: 20D avg volume below (.+) shares$",
        r"Likidite yetersiz: 20 günlük ort. adet \1 altında",
    ),
    (r"^Stale data: (.+)$", r"Veri eski: \1"),
    (r"^Stale data$", "Veri eski"),
    (r"^Signal suppressed: data is stale or incomplete$", "Sinyal bastırıldı: veri eski/eksik"),
    (r"^Signal suppressed: liquidity filter failed$", "Sinyal bastırıldı: likidite filtresi"),
    (
        r"^Buy-level score but no valid risk plan \(ATR unavailable\)$",
        "Puan AL seviyesinde ama risk planı yok (ATR hesaplanamadı)",
    ),
    (
        r"^Buy-level score but reward/risk ([\d.]+) \(capped by overhead resistance\) < minimum "
        r"([\d.]+)$",
        r"Puan AL seviyesinde ama getiri/risk \1 (üstteki dirençle sınırlı) < asgari \2",
    ),
    (
        r"^Buy-level score but reward/risk ([\d.]+) < minimum ([\d.]+)$",
        r"Puan AL seviyesinde ama getiri/risk \1 < asgari \2",
    ),
    (
        r"^Buy-level score but buys are blocked in a BEAR regime$",
        "Puan AL seviyesinde ama AYI piyasasında alım kapalı",
    ),
    (
        r"^Score below the (\w+) regime buy threshold \((\d+)\)$",
        r"Puan \1 piyasa durumunun AL eşiğinin (\2) altında",
    ),
    # news
    (
        r"^No material KAP news in the last (\S+) days \(neutral\)$",
        r"Son \1 günde önemli KAP haberi yok (nötr)",
    ),
    (r"^KAP news unavailable for this scan$", "Bu taramada KAP haberleri alınamadı"),
    (r"^News/KAP engine not enabled \(Phase 3\)$", "Haber/KAP motoru kapalı"),
]
_COMPILED = [(re.compile(p), r) for p, r in _PATTERNS]
_REGIME_WORDS = {r.value: t for r, t in REGIME_TR.items()}


def tr(text: str) -> str:
    """Translate one engine explanation line to Turkish (unknown lines pass through)."""
    if text.endswith(" (insufficient data)"):
        return tr(text[: -len(" (insufficient data)")]) + " (yetersiz veri)"
    for pattern, repl in _COMPILED:
        if pattern.match(text):
            out = pattern.sub(repl, text)
            for en, tr_word in _REGIME_WORDS.items():
                out = out.replace(f"Puan {en} ", f"Puan {tr_word} ")
            return out
    return text


def signal_text(signal: SignalType) -> str:
    return SIGNAL_TR.get(signal, signal.value)


def regime_text(regime: MarketRegime) -> str:
    return REGIME_TR.get(regime, regime.value)


def money(x: float | None, digits: int = 2) -> str:
    """Turkish number format: 1.234.567,89"""
    if x is None:
        return "-"
    s = f"{x:,.{digits}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")
