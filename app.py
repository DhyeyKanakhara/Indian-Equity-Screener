"""Indian Equity Stock Screener — Flask backend.

Serves a standalone HTML/JS frontend and exposes two JSON APIs:
  POST /api/run    -> run the screener, return ranked results
  POST /api/download -> return results as CSV file

Run:
    python app.py
Then open http://127.0.0.1:5000  (or http://YOUR_IP:5000 from another device)
"""

from __future__ import annotations

import io
import logging
import pandas as pd
from flask import Flask, render_template, request, jsonify, Response

from screener_engine import run_screener, FactorWeights, Filters, DEFAULT_TICKERS

app = Flask(__name__)
logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _opt_float(v):
    """Parse optional float; return None if missing / invalid."""
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _opt_pct(v):
    """Parse optional percentage input (e.g. 15) and convert to fraction (0.15)."""
    f = _opt_float(v)
    return None if f is None else f / 100.0


def _build_tickers(data) -> list[str]:
    raw = data.get("tickers") or ""
    tickers = [t.strip().upper() for t in raw.replace("\n", ",").split(",") if t.strip()]
    return tickers if tickers else DEFAULT_TICKERS


def _build_filters(data) -> Filters:
    f = data.get("filters") or {}
    return Filters(
        pe_min=_opt_float(f.get("pe_min")),
        pe_max=_opt_float(f.get("pe_max")),
        pb_min=_opt_float(f.get("pb_min")),
        pb_max=_opt_float(f.get("pb_max")),
        roe_min=_opt_pct(f.get("roe_min")),
        roce_min=_opt_pct(f.get("roce_min")),
        de_max=_opt_float(f.get("de_max")),
        market_cap_min_cr=_opt_float(f.get("market_cap_min_cr")),
        mom6m_min=_opt_pct(f.get("mom6m_min")),
        mom12m_min=_opt_pct(f.get("mom12m_min")),
    )


def _build_weights(data) -> FactorWeights:
    w = data.get("weights") or {}
    return FactorWeights(
        pe=_opt_float(w.get("pe", 0.10)),
        pb=_opt_float(w.get("pb", 0.05)),
        roe=_opt_float(w.get("roe", 0.15)),
        roce=_opt_float(w.get("roce", 0.15)),
        de=_opt_float(w.get("de", 0.10)),
        mom6m=_opt_float(w.get("mom6m", 0.15)),
        mom12m=_opt_float(w.get("mom12m", 0.15)),
    )


def _df_to_records(df: pd.DataFrame) -> list[dict]:
    """Convert dataframe to JSON-safe list of dicts (NaN -> None)."""
    df = df.where(pd.notna(df), None)
    return df.to_dict("records")


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html", default_tickers=", ".join(DEFAULT_TICKERS))


@app.route("/api/run", methods=["POST"])
def api_run():
    data = request.get_json(force=True) or {}
    tickers = _build_tickers(data)
    weights = _build_weights(data)
    filters = _build_filters(data)

    try:
        df = run_screener(tickers, weights, filters)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Screener failed")
        return jsonify({"error": str(exc)}), 500

    if df.empty:
        return jsonify({"results": [], "count": 0, "message": "No data returned or no stocks passed the filters."})

    return jsonify({"results": _df_to_records(df), "count": len(df)})


@app.route("/api/download", methods=["POST"])
def api_download():
    data = request.get_json(force=True) or {}
    tickers = _build_tickers(data)
    weights = _build_weights(data)
    filters = _build_filters(data)
    fmt = (data.get("format") or "csv").lower()

    df = run_screener(tickers, weights, filters)
    if df.empty:
        return jsonify({"error": "No data to download"}), 400

    out = pd.DataFrame()
    out["Rank"] = df["rank"] if "rank" in df.columns else range(1, len(df) + 1)
    out["Ticker"] = df["ticker"]
    out["Name"] = df.get("name", "")
    out["Sector"] = df.get("sector", "")
    out["Composite Score"] = df.get("composite_score")
    out["P/E"] = df.get("trailing_pe")
    out["P/B"] = df.get("price_to_book")
    out["ROE"] = df.get("roe")
    out["ROCE"] = df.get("roce")
    out["Debt/Equity"] = df.get("debt_to_equity")
    out["6M Return"] = df.get("return_6m")
    out["12M Return"] = df.get("return_12m")
    out["Market Cap (INR)"] = df.get("market_cap_inr")

    if fmt == "excel":
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            out.to_excel(writer, index=False, sheet_name="Results")
        buffer.seek(0)
        return Response(
            buffer.read(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="screener_results.xlsx"'},
        )

    buffer = io.StringIO()
    out.to_csv(buffer, index=False)
    csv_data = buffer.getvalue()

    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": 'attachment; filename="screener_results.csv"'},
    )


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
