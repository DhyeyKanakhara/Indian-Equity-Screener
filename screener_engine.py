"""Indian equity stock screener engine.

Fetches NSE/BSE data via yfinance (``.NS`` / ``.BO`` tickers), computes
value / quality / momentum factors, normalizes them into scores, and produces
a composite-ranked list of stocks.

Factors computed
----------------
Value:       P/E, P/B
Quality:     ROE, ROCE, Debt/Equity
Momentum:    6-month return, 12-month return

Each factor is converted to a percentile rank where *higher is always better*
(for cheapness factors like P/E the rank is inverted). A weighted composite
score then ranks the universe. Filters can exclude stocks that don't meet
minimum quality / value thresholds.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
import yfinance as yf

from tickers import DEFAULT_TICKERS

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
@dataclass
class FactorWeights:
    """Weights for the composite score. All non-negative; normalized internally."""

    pe: float = 0.10
    pb: float = 0.05
    roe: float = 0.15
    roce: float = 0.15
    de: float = 0.10
    mom6m: float = 0.15
    mom12m: float = 0.15

    def as_dict(self) -> dict[str, float]:
        return {
            "pe": self.pe,
            "pb": self.pb,
            "roe": self.roe,
            "roce": self.roce,
            "de": self.de,
            "mom6m": self.mom6m,
            "mom12m": self.mom12m,
        }


@dataclass
class Filters:
    """Optional screening filters. None means unconstrained."""

    pe_min: Optional[float] = None
    pe_max: Optional[float] = None
    pb_min: Optional[float] = None
    pb_max: Optional[float] = None
    roe_min: Optional[float] = None          # fraction, e.g. 0.15 for 15%
    roce_min: Optional[float] = None         # fraction
    de_max: Optional[float] = None
    market_cap_min_cr: Optional[float] = None  # in crores of INR
    mom6m_min: Optional[float] = None        # fraction
    mom12m_min: Optional[float] = None       # fraction


# Column names used internally
TICKER = "ticker"
NAME = "name"
SECTOR = "sector"
MARKET_CAP = "market_cap_inr"
PE = "trailing_pe"
PB = "price_to_book"
ROE = "roe"
ROCE = "roce"
DE = "debt_to_equity"
MOM6M = "return_6m"
MOM12M = "return_12m"
SCORE = "composite_score"

# factor -> (column, higher_is_better)
_FACTOR_SPEC: dict[str, tuple[str, bool]] = {
    "pe": (PE, False),
    "pb": (PB, False),
    "roe": (ROE, True),
    "roce": (ROCE, True),
    "de": (DE, False),
    "mom6m": (MOM6M, True),
    "mom12m": (MOM12M, True),
}

# Display-friendly renames for the UI
_DISPLAY_COLUMNS: list[tuple[str, str]] = [
    ("rank", "Rank"),
    ("ticker", "Ticker"),
    ("name", "Name"),
    ("sector", "Sector"),
    ("composite_score", "Score"),
    ("trailing_pe", "P/E"),
    ("price_to_book", "P/B"),
    ("roe", "ROE %"),
    ("roce", "ROCE %"),
    ("debt_to_equity", "D/E"),
    ("return_6m", "6M Return %"),
    ("return_12m", "12M Return %"),
    ("market_cap_inr", "Mkt Cap (₹ Cr)"),
]


# --------------------------------------------------------------------------- #
# Data fetching
# --------------------------------------------------------------------------- #
def _compute_roce(tk: yf.Ticker) -> Optional[float]:
    """Return on Capital Employed = EBIT / (Total Assets - Current Liabilities).

    Uses the latest annual income statement and balance sheet. Returns None when
    the data is unavailable (common for some tickers on yfinance).
    """
    try:
        income = tk.financials
        balance = tk.balance_sheet
        if income is None or balance is None or income.empty or balance.empty:
            return None

        ebit = None
        for row in ("EBIT", "Operating Income"):
            if row in income.index:
                ebit = float(income.loc[row].iloc[0])
                break

        total_assets = None
        current_liabilities = None
        if "Total Assets" in balance.index:
            total_assets = float(balance.loc["Total Assets"].iloc[0])
        # Newer yfinance balance sheets name this row "Current Liabilities".
        for row in ("Total Current Liabilities", "Current Liabilities"):
            if row in balance.index:
                current_liabilities = float(balance.loc[row].iloc[0])
                break

        if ebit is None or total_assets is None or current_liabilities is None:
            return None

        capital_employed = total_assets - current_liabilities
        if not capital_employed or capital_employed <= 0:
            return None
        return ebit / capital_employed
    except Exception as exc:  # noqa: BLE001 - one bad ticker must not abort the run
        logger.warning("ROCE lookup failed: %s", exc)
        return None


def _fetch_one(ticker: str) -> dict:
    """Fetch fundamental + price data for a single ticker."""
    tk = yf.Ticker(ticker)
    info = tk.info or {}

    record: dict = {
        TICKER: ticker,
        NAME: info.get("shortName") or info.get("longName") or ticker,
        SECTOR: info.get("sector"),
        MARKET_CAP: info.get("marketCap"),
        PE: info.get("trailingPE"),
        PB: info.get("priceToBook"),
        ROE: info.get("returnOnEquity"),
        DE: info.get("debtToEquity"),
        ROCE: None,
        MOM6M: None,
        MOM12M: None,
    }

    # ROCE from financial statements (yfinance.info doesn't expose it directly).
    record[ROCE] = _compute_roce(tk)

    # Momentum from ~1 year of daily prices. One history call covers both 6M
    # and 12M returns (approx. 126 trading days = 6 months).
    try:
        hist = tk.history(period="1y")
        if hist is not None and not hist.empty:
            close = hist["Close"].dropna()
            if len(close) >= 2:
                record[MOM12M] = float(close.iloc[-1] / close.iloc[0] - 1.0)
                six_month_idx = max(0, len(close) - 126)
                if len(close) > six_month_idx:
                    record[MOM6M] = float(close.iloc[-1] / close.iloc[six_month_idx] - 1.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Price history failed for %s: %s", ticker, exc)

    return record


# --------------------------------------------------------------------------- #
# Filtering & scoring
# --------------------------------------------------------------------------- #
def apply_filters(df: pd.DataFrame, filters: Optional[Filters]) -> pd.DataFrame:
    """Keep only rows satisfying every non-None filter constraint."""
    if filters is None:
        return df

    if filters.pe_min is not None:
        df = df[df[PE] >= filters.pe_min]
    if filters.pe_max is not None:
        df = df[df[PE] <= filters.pe_max]
    if filters.pb_min is not None:
        df = df[df[PB] >= filters.pb_min]
    if filters.pb_max is not None:
        df = df[df[PB] <= filters.pb_max]
    if filters.roe_min is not None:
        df = df[df[ROE] >= filters.roe_min]
    if filters.roce_min is not None:
        df = df[df[ROCE] >= filters.roce_min]
    if filters.de_max is not None:
        df = df[df[DE] <= filters.de_max]
    if filters.market_cap_min_cr is not None:
        df = df[df[MARKET_CAP] >= filters.market_cap_min_cr * 1e7]
    if filters.mom6m_min is not None:
        df = df[df[MOM6M] >= filters.mom6m_min]
    if filters.mom12m_min is not None:
        df = df[df[MOM12M] >= filters.mom12m_min]
    return df


def _percentile_score(series: pd.Series, higher_is_better: bool) -> pd.Series:
    """Percentile rank (0-1) where higher means more attractive.

    Cheapness factors (P/E, P/B, D/E) are inverted so that a lower raw value
    yields a higher score. NaN inputs yield NaN scores.
    """
    ranked = series.rank(pct=True)
    if not higher_is_better:
        ranked = 1.0 - ranked
    return ranked


def compute_composite(df: pd.DataFrame, weights: FactorWeights) -> pd.DataFrame:
    """Add ``composite_score`` and rank columns to the dataframe.

    The composite is a weighted average of per-factor percentile scores, using
    only factors with available data for each stock (weights are re-normalized
    per row so missing data doesn't unfairly punish a stock).
    """
    w = pd.Series(weights.as_dict())
    scores = pd.DataFrame(index=df.index)

    for key, (col, higher_better) in _FACTOR_SPEC.items():
        scores[key] = _percentile_score(df[col], higher_better)

    available = scores.notna()
    weighted_sum = (scores * w).sum(axis=1)
    weight_total = (available * w).sum(axis=1)
    df = df.copy()
    df[SCORE] = weighted_sum / weight_total.replace(0, np.nan)
    df = df.sort_values(SCORE, ascending=False)
    df["rank"] = np.arange(1, len(df) + 1)
    return df


def fetch_universe(tickers: list[str]) -> pd.DataFrame:
    """Fetch factor data for every ticker in the list."""
    records = [_fetch_one(t) for t in tickers]
    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    # Ensure expected columns exist even when everything is missing.
    for col in [NAME, SECTOR, MARKET_CAP, PE, PB, ROE, ROCE, DE, MOM6M, MOM12M]:
        if col not in df.columns:
            df[col] = np.nan
    return df


def run_screener(
    tickers: list[str],
    weights: Optional[FactorWeights] = None,
    filters: Optional[Filters] = None,
) -> pd.DataFrame:
    """End-to-end: fetch -> filter -> score -> rank."""
    if not tickers:
        return pd.DataFrame()
    df = fetch_universe(tickers)
    if df.empty:
        return df
    df = apply_filters(df, filters)
    if df.empty:
        return df
    df = compute_composite(df, weights or FactorWeights())
    return df.reset_index(drop=True)


def for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy renamed and prettified for the Streamlit table."""
    if df.empty:
        return pd.DataFrame()
    show = pd.DataFrame()
    for src, dst in _DISPLAY_COLUMNS:
        show[dst] = df[src] if src in df.columns else np.nan

    # Convert fractions to percentages and market cap to crores.
    pct_cols = ["ROE %", "ROCE %", "6M Return %", "12M Return %"]
    for c in pct_cols:
        if c in show.columns:
            show[c] = show[c] * 100.0
    if "Mkt Cap (₹ Cr)" in show.columns:
        show["Mkt Cap (₹ Cr)"] = show["Mkt Cap (₹ Cr)"] / 1e7

    return show
