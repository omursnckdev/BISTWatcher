"""News / KAP factor (0-15 by default, spec §35).

Net impact at cutoff time *t* over events published at or before *t*::

    S = sum_i sentiment_i * importance_i * confidence_i * source_quality_i
              * scale_i * reaction_i * decay(age_i)

    points = weight / 2 * (1 + tanh(S / saturation))

No news (S = 0) gives the neutral midpoint (7.5 / 15); strongly positive news
approaches 15, strongly negative news approaches 0. A price reaction is only used
once it is observable (``reaction_known_at <= t``).
"""

from __future__ import annotations

import bisect
import math
from datetime import datetime, time

import pandas as pd

from bist_quant.config import NewsSettings
from bist_quant.data.market_data import ISTANBUL_TZ
from bist_quant.models.news import NewsEvent
from bist_quant.models.signals import ComponentScore
from bist_quant.news.decay import decay_weight


def _parse_time(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class NewsBook:
    """Classified events per symbol with point-in-time scoring."""

    def __init__(self, events: dict[str, list[NewsEvent]], cfg: NewsSettings) -> None:
        self.cfg = cfg
        self.events = {
            s: sorted(ev, key=lambda e: e.article.published_at) for s, ev in events.items()
        }
        self._times = {
            s: [e.article.published_at.timestamp() for e in ev] for s, ev in self.events.items()
        }
        self.cutoff_time = _parse_time(cfg.cutoff_time)

    def cutoff_for(self, day: pd.Timestamp | datetime) -> datetime:
        """News deadline for a signal computed on session ``day``."""
        d = day.date() if hasattr(day, "date") else day
        return datetime.combine(d, self.cutoff_time, ISTANBUL_TZ)

    def window(self, symbol: str, cutoff: datetime) -> list[NewsEvent]:
        times = self._times.get(symbol)
        if not times:
            return []
        hi = bisect.bisect_right(times, cutoff.timestamp())
        lo = bisect.bisect_left(times, cutoff.timestamp() - self.cfg.decay.max_age_days * 86400)
        return self.events[symbol][lo:hi]

    def contributions(self, symbol: str, cutoff: datetime) -> list[tuple[NewsEvent, float, float]]:
        """(event, impact, decay) for every event affecting the score at ``cutoff``."""
        out = []
        rc = self.cfg.reaction
        for ev in self.window(symbol, cutoff):
            c = ev.classification
            if c.importance < self.cfg.min_importance:
                continue
            age_h = (cutoff - ev.article.published_at).total_seconds() / 3600
            w = decay_weight(age_h, c.impact_horizon, self.cfg.decay)
            if w <= 0:
                continue
            sentiment, mult = c.sentiment, 1.0
            known = ev.reaction_known_at is not None and ev.reaction_known_at <= cutoff
            if rc.enabled and known:
                if (
                    ev.implied_sentiment is not None
                    and c.importance >= rc.min_importance_for_inference
                ):
                    sentiment = ev.implied_sentiment
                else:
                    mult = ev.reaction_multiplier
            base = ev.source_quality * ev.scale_multiplier
            impact = sentiment * c.importance * c.confidence * base * mult * w
            if impact != 0:
                out.append((ev, impact, w))
        return out

    def net_impact(self, symbol: str, cutoff: datetime) -> float:
        return sum(i for _, i, _ in self.contributions(symbol, cutoff))

    def component(self, symbol: str, cutoff: datetime, weight: float) -> ComponentScore:
        contribs = self.contributions(symbol, cutoff)
        net = sum(i for _, i, _ in contribs)
        points = weight / 2 * (1 + math.tanh(net / self.cfg.saturation))
        comp = ComponentScore(name="news", points=points, max_points=weight)
        if not contribs:
            comp.notes.append(
                f"No material KAP news in the last {self.cfg.decay.max_age_days:g} days (neutral)"
            )
            return comp
        for ev, impact, _decay in sorted(contribs, key=lambda x: -abs(x[1]))[:5]:
            age_d = (cutoff - ev.article.published_at).total_seconds() / 86400
            c = ev.classification
            text = (
                f"KAP {ev.article.published_at:%d.%m %H:%M} {c.event_type.value}: "
                f"{(c.summary or ev.article.subject)[:90]} "
                f"(impact {impact:+.2f}, {age_d:.1f}d old"
            )
            if ev.reaction and ev.reaction_known_at and ev.reaction_known_at <= cutoff:
                text += f", {ev.reaction.lower().replace('_', ' ')}"
            if ev.scale_multiplier != 1.0:
                text += f", size x{ev.scale_multiplier:.2f}"
            text += ")"
            (comp.positive_factors if impact > 0 else comp.negative_factors).append(text)
        return comp
