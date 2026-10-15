# Indian Equity Stock Screener

A small, extensible stock screener for NSE/BSE equities that blends **value**, **quality**, and **momentum** into a single composite ranking. Built with Python, `yfinance`, `pandas`, and **Streamlit**.

## What it does

1. **Pulls data** for a list of Indian tickers (`.NS` for NSE, `.BO` for BSE) via `yfinance`.
2. **Computes factors:** P/E, P/B, ROE, ROCE, debt/equity, 6-month return, 12-month return.
3. **Filters** the universe with configurable thresholds.
4. **Ranks** stocks by a weighted composite score (percentile-based, so no single raw scale dominates).
5. **Displays** a ranked table in a Streamlit UI with CSV export.

## Project structure

```
indian_equity_screener/
├── README.md
├── requirements.txt
├── .gitignore
├── tickers.py          # default Nifty 50 (.NS) ticker universe
├── screener_engine.py  # fetch, factor computation, filtering, ranking
└── app.py              # Streamlit UI
```

## Setup

```bash
cd indian_equity_screener
python -m venv .venv
.venv\Scripts\activate        # Windows
# or: source .venv/bin/activate  # macOS/Linux

pip install -r requirements.txt
streamlit run app.py
```

## The factors — and why each one matters

This is the "curiosity" part: the score is only useful if you know *why* each input is there.

| Factor | What it measures | Why it's used | Direction |
|--------|-----------------|---------------|-----------|
| **P/E** | Price per ₹1 of trailing earnings | **Value.** How expensive the stock is relative to its earnings power. A low P/E can mean the market is underpricing earnings (or pricing in trouble). | Lower is better |
| **P/B** | Price per ₹1 of book value (net assets) | **Value.** Especially meaningful for banks, insurers, and capital-intensive firms where book value approximates tangible resources. | Lower is better |
| **ROE** | Net profit ÷ shareholder equity | **Quality.** How efficiently management generates profit from owners' capital. Persistently high ROE often signals a durable competitive advantage. | Higher is better |
| **ROCE** | EBIT ÷ capital employed (assets − current liabilities) | **Quality.** Like ROE, but includes debt-financed capital. Better for comparing capital-intensive companies and for spotting firms that earn well on *all* capital, not just equity. Computed from latest annual financials. | Higher is better |
| **Debt/Equity** | Total debt ÷ shareholder equity | **Quality/risk.** Leverage buffer. Low D/E means more cushion through downturns; high D/E magnifies both returns and risk. | Lower is better |
| **6M return** | Price change over ~6 months | **Momentum.** Trends tend to persist in the short-to-medium term; price already reflects the market's collective view. | Higher is better |
| **12M return** | Price change over ~12 months | **Momentum.** Captures longer-term trend strength; helps separate genuine momentum from a single-month spike. | Higher is better |

### Composite score

Each factor is converted to a **percentile rank** across the screened universe (0 = worst, 1 = best), with cheapness factors inverted so that lower P/E, P/B, and D/E produce higher scores. Factors are then blended with weights (defaults: value 15%, quality 40%, momentum 30%). Weights are re-normalized per stock using only the factors that have data, so a missing ROCE doesn't unfairly punish an otherwise good stock.

> **Caveats:** `yfinance` data can be delayed or incomplete (ROCE is missing for some tickers), ratios are trailing, and a low P/E is not automatically "cheap" if earnings are collapsing. Treat this as a research starting point, not a buy/sell recommendation.

## Configuring filters & weights

In the Streamlit sidebar you can:

* **Universe:** Use the default Nifty 50 list or paste any `.NS`/`.BO` tickers.
* **Filters:** min/max P/E and P/B, minimum ROE/ROCE, maximum D/E, minimum market cap (₹ crore), and minimum 6M/12M returns. Leave blank to ignore.
* **Weights:** Slide each factor's weight to tilt the composite toward value, quality, or momentum.

## Share / deploy so others can view it

The quickest way to give others a public link they can open in a browser is **Streamlit Community Cloud** (free):

1. Push this folder to a GitHub repository.
2. Go to [streamlit.io/cloud](https://streamlit.io/cloud), sign in with GitHub, and click **"New app"**.
3. Select your repo, branch (`main`), and main file path: `indian_equity_screener/app.py`.
4. Click **Deploy**. Streamlit gives you a public URL (e.g. `https://your-app.streamlit.app`) you can share with anyone — they only need a browser, no Python install.

### Alternative: quick temporary share (no GitHub)

If you just want a link right now for someone to peek at:

```bash
# run locally
streamlit run app.py

# in another terminal, expose it to the internet (install ngrok first)
ngrok http 8501
```

ngrok prints a public URL (like `https://abc123.ngrok-free.app`) that works while your computer stays on and the app keeps running.

## Extending it

* Add more tickers to `tickers.py` or paste them in the UI.
* Add new factors in `screener_engine.py` (compute → add to `_FACTOR_SPEC` with a direction → weight in `FactorWeights`).
* Pipe in `nsepython` or Screener.in exports instead of `yfinance` if you prefer exchange-native sources.

## License

MIT — free to use and modify. Not financial advice.