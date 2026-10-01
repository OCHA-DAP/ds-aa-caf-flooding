"""Render the CAR floods site into site_build/ (gitignored, never published as-is).

    site_build/index.html                      landing page (one card per product)
    site_build/impact-vs-rainfall/index.html   the analysis, with every download embedded

Run scripts/build_data.py first. The pages are self-contained (CSS, JS, data and
downloadable files inlined) so that staticrypt encrypts all of it; see
scripts/publish.sh for the encrypt + gh-pages step.
"""

import base64
import gzip
import html
import json
import re
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import linregress, pearsonr, spearmanr, t

ROOT = Path(__file__).parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
SITE = ROOT / "site"
OUT = ROOT / "site_build"

YEARS = list(range(2021, 2026))
# Alerts in the public HDX version of the compilation (resource updated 2025-01-21).
HDX_ROWS = 174
HDX_URL = "https://data.humdata.org/dataset/republique-centrafricaine-situation-des-inondations"

RAW_FILES = {
    "OCHA_CAR_donnees_inondations_compil_2021-2025.xlsx": "OCHA CAR flood compilation as received "
    "(all sheets; the analysis uses “DATA FOR PBI”). Not for redistribution.",
    "era5_caf_adm0_monthly.csv": "ERA5 national zonal stats, monthly mean daily rate (mm/day), "
    "from the prod DB table public.era5",
    "imerg_caf_adm0_daily.csv": "IMERG late v7 national zonal stats, daily (mm/day), from the "
    "prod DB table public.imerg",
}
PROCESSED_FILES = {
    "impact_events_adm3.csv": "One row per alert with adm1–adm3 names and pcodes, how it was "
    "matched (match_method) and the impact columns in English",
    "impact_monthly.csv": "Alerts, people and households affected per month, zero-filled 2021–2025",
    "impact_adm3_by_year.csv": "Alerts and people affected per commune (adm3) and year",
    "impact_adm1_by_year.csv": "Alerts and people affected per prefecture (adm1) and year",
    "rain_monthly.csv": "ERA5 and IMERG monthly national totals (mm), cumulative since January, "
    "2001–2020 average and anomaly",
    "rain_annual.csv": "ERA5 and IMERG annual national totals (mm), complete years only",
    "rain_trends.csv": "Linear trend per calendar month (month 0 = annual), mm/decade with 95% CI",
    "impact_vs_rain_annual.csv": "People affected per year alongside each product's annual rainfall",
    "impact_vs_rain_monthly.csv": "People affected per month alongside monthly rainfall and anomalies",
    "cumulative_rain_correlation.csv": "Correlation of rain accumulated since January with that "
    "year's people affected, per product and month",
    "adm3_impact.geojson": "Commune (adm3) boundaries, simplified, with totals 2021–2025",
    "adm1_impact.geojson": "Prefecture (adm1) boundaries, simplified, with totals 2021–2025",
}
MIME = {
    ".csv": "text/csv",
    ".geojson": "application/geo+json",
    ".txt": "text/plain",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def r6(x):
    """Round floats for the embedded JSON; NaN -> None."""
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    if isinstance(x, (float, np.floating)):
        return round(float(x), 2)
    if isinstance(x, np.integer):
        return int(x)
    return x


def records(df: pd.DataFrame, cols: dict) -> list[dict]:
    return [{k: r6(row[v]) for k, v in cols.items()} for _, row in df.iterrows()]


def embed_json(obj) -> str:
    # Safe inside <script type="application/json">: no "<" survives, so nothing
    # in the data can close the tag or open a comment.
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def file_entry(group: str, path: Path, desc: str) -> dict:
    data = path.read_bytes()
    gz = path.suffix != ".xlsx"  # xlsx is already a zip archive
    payload = gzip.compress(data, mtime=0) if gz else data
    return {
        "group": group,
        "name": path.name,
        "desc": desc,
        "size": len(data),
        "mime": MIME[path.suffix],
        "gz": gz,
        "b64": base64.b64encode(payload).decode("ascii"),
    }


# Slots that take code or JSON verbatim; every other value is escaped as text.
RAW_SLOTS = {"CSS", "JS", "HERO_JS", "DATA", "FILES"}


def fill(template: str, values: dict) -> str:
    out = template
    for k, v in values.items():
        out = out.replace("{{" + k + "}}", v if k in RAW_SLOTS else html.escape(str(v)))
    left = re.findall(r"\{\{[A-Z0-9_]+\}\}", out)
    if left:
        raise ValueError(f"unfilled placeholders: {sorted(set(left))}")
    return out


def readme(today: str) -> str:
    lines = [
        "CAR floods: impact vs rainfall - processed data",
        f"Built {today} by scripts/build_data.py in OCHA-DAP/ds-aa-caf-flooding.",
        "",
        "Impact: OCHA CAR flood compilation (sheet DATA FOR PBI), 2021-2025. People affected =",
        "'Individus affecté', households = 'Ménage affecté'; blanks count as zero in sums.",
        "Rainfall: national (adm0) zonal means of ERA5 (monthly) and IMERG late v7 (daily),",
        "converted to monthly totals in mm. Anomalies are vs each product's 2001-2020 mean.",
        "",
        "Files:",
    ]
    for name, desc in PROCESSED_FILES.items():
        lines.append(f"  {name}: {desc}")
    lines += [
        "",
        "match_method in impact_events_adm3.csv:",
        "  name          commune name matched an adm3 name after normalising",
        "  name_fix      matched after a spelling fix (e.g. '4e Arrondissement' -> 'Arrondissement 4')",
        "  row_override  reassigned by hand from the sous-prefecture / locality columns",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    today = date.today()
    ev = pd.read_csv(PROC / "impact_events_adm3.csv", parse_dates=["date_alert"])
    monthly = pd.read_csv(PROC / "impact_monthly.csv")
    adm3_year = pd.read_csv(PROC / "impact_adm3_by_year.csv")
    adm1_year = pd.read_csv(PROC / "impact_adm1_by_year.csv")
    rain_m = pd.read_csv(PROC / "rain_monthly.csv")
    rain_a = pd.read_csv(PROC / "rain_annual.csv")
    trends = pd.read_csv(PROC / "rain_trends.csv")
    comp_a = pd.read_csv(PROC / "impact_vs_rain_annual.csv")
    comp_m = pd.read_csv(PROC / "impact_vs_rain_monthly.csv")
    corr = pd.read_csv(PROC / "cumulative_rain_correlation.csv")
    geo3 = json.loads((PROC / "adm3_impact.geojson").read_text())
    geo1 = json.loads((PROC / "adm1_impact.geojson").read_text())

    # --- summary numbers for the prose -----------------------------------
    people = ev["people_affected"].sum()
    by_year = comp_a.set_index("year")["people_affected"]
    worst = int(by_year.idxmax())
    jaso = monthly.loc[monthly["month"].between(7, 10), "people_affected"].sum() / people
    adm1_tot = adm1_year.groupby("adm1_name")["people_affected"].sum().sort_values(ascending=False)
    top2 = adm1_tot.iloc[:2]
    c12 = corr[corr["through_month"] == 12].set_index("product")
    tr = trends.set_index(["product", "period", "month"])
    era5_98, imerg_98 = tr.loc[("ERA5", "1998-2025", 0)], tr.loc[("IMERG", "1998-2025", 0)]
    era5_81 = tr.loc[("ERA5", "1981-2025", 0)]
    era5_a = rain_a[rain_a["product"] == "ERA5"].set_index("year")["precip_mm"]
    clim_era5 = rain_m[rain_m["product"] == "ERA5"].groupby("month")["clim_mm"].first().sum()
    n_drier = int((era5_a.loc[1981:2025] <= era5_a.loc[YEARS].max()).sum())
    rank_text = (
        f"the five impact years 2021–2025 are the five driest years in ERA5's {len(era5_a.loc[1981:2025])}-year record"
        if n_drier == len(YEARS)
        else f"all five impact years fall among ERA5's {n_drier} driest years since 1981"
    )
    both = comp_m.dropna(subset=["era5_precip_mm", "imerg_precip_mm"])
    r_products = pearsonr(both["era5_precip_mm"], both["imerg_precip_mm"])[0]
    df_n = len(comp_a) - 2
    t_crit = t.ppf(0.975, df_n)
    r_crit = t_crit / np.sqrt(t_crit**2 + df_n)

    monthly_corr = []
    for prod in ["ERA5", "IMERG"]:
        for var, col in [("total", "precip_mm"), ("anom", "anomaly_mm")]:
            c = comp_m.dropna(subset=[f"{prod.lower()}_{col}"])
            x = c[f"{prod.lower()}_{col}"]
            monthly_corr.append(
                {
                    "product": prod,
                    "var": var,
                    "n": len(c),
                    "r": round(pearsonr(x, c["people_affected"])[0], 3),
                    "rho": round(spearmanr(x, c["people_affected"])[0], 3),
                }
            )
    mc = {(d["product"], d["var"]): d for d in monthly_corr}

    # trend lines 1998-2025 for the annual chart
    trend_lines = {}
    for prod in ["ERA5", "IMERG"]:
        s = rain_a[(rain_a["product"] == prod) & rain_a["year"].between(1998, 2025)]
        res = linregress(s["year"], s["precip_mm"])
        trend_lines[prod] = {
            "x0": 1998,
            "y0": round(res.intercept + res.slope * 1998, 1),
            "x1": 2025,
            "y1": round(res.intercept + res.slope * 2025, 1),
        }

    n_hh = ev["households_affected"].sum()
    fmt = lambda v: f"{v:,.0f}"  # noqa: E731
    signed = lambda v: ("+" if v >= 0 else "−") + f"{abs(v):.0f}"  # noqa: E731
    first, last = ev["date_alert"].min(), ev["date_alert"].max()
    n_2025 = int((ev["year"] == 2025).sum())
    methods = ev["match_method"].value_counts()

    values = {
        "PERIOD": f"{first:%b %Y} – {last:%b %Y}",
        "FIRST_ALERT": f"{first.day} {first:%b %Y}",
        "LAST_ALERT": f"{last.day} {last:%b %Y}",
        "N_EVENTS": fmt(len(ev)),
        "N_PEOPLE": fmt(people),
        "N_HOUSEHOLDS": fmt(n_hh),
        "N_ADM3": ev["adm3_src"].nunique(),
        "N_ADM3_ALL": len(geo3["features"]),
        "N_ADM1": ev["adm1_src"].nunique(),
        "N_ADM1_ALL": len(geo1["features"]),
        "WORST_YEAR": worst,
        "WORST_PEOPLE": fmt(by_year.max()),
        "WORST_PCT": f"{100 * by_year.max() / people:.0f}",
        "PCT_JASO": f"{100 * jaso:.0f}",
        "TOP2_TEXT": (
            f"{top2.index[0]} and {top2.index[1]} together account for "
            f"{100 * top2.sum() / people:.0f}% of people affected."
        ),
        "R_ERA5": f"{c12.loc['ERA5', 'pearson_r']:.2f}",
        "R_IMERG": f"{c12.loc['IMERG', 'pearson_r']:.2f}".replace("-", "−"),
        "RHO_TOTAL": f"{np.mean([mc[('ERA5', 'total')]['rho'], mc[('IMERG', 'total')]['rho']]):.1f}",
        "RHO_ANOM_ERA5": f"{mc[('ERA5', 'anom')]['rho']:.2f}".replace("-", "−"),
        "RHO_ANOM_IMERG": f"{mc[('IMERG', 'anom')]['rho']:.2f}".replace("-", "−"),
        "TREND_ERA5": signed(era5_98["slope_mm_per_decade"]),
        "TREND_ERA5_ABS": f"{abs(era5_98['slope_mm_per_decade']):.0f}",
        "TREND_ERA5_CI": f"{era5_98['ci95_mm_per_decade']:.0f}",
        "TREND_ERA5_FULL": signed(era5_81["slope_mm_per_decade"]),
        "TREND_ERA5_FULL_PCT": f"{100 * abs(era5_81['slope_mm_per_decade']) / clim_era5:.0f}",
        "TREND_IMERG": signed(imerg_98["slope_mm_per_decade"]),
        "TREND_IMERG_CI": f"{imerg_98['ci95_mm_per_decade']:.0f}",
        "ERA5_RANK_TEXT": rank_text,
        "R_PRODUCTS": f"{r_products:.2f}",
        "R_CRIT": f"{r_crit:.2f}",
        "N_HDX": HDX_ROWS,
        "N_EXTRA_2024": len(ev) - n_2025 - HDX_ROWS,
        "N_2025": n_2025,
        "N_NULL_PEOPLE": int(ev["people_affected"].isna().sum()),
        "N_NAME": int(methods.get("name", 0)),
        "N_NAME_FIX": int(methods.get("name_fix", 0)),
        "N_OVERRIDE": int(methods.get("row_override", 0)),
        "BUILT": today.isoformat(),
        "BUILT_MONTH": f"{today:%b %Y}",
    }

    # --- data for the charts --------------------------------------------
    clim = rain_m.groupby(["product", "month"])["clim_mm"].first().reset_index()
    adm3 = []
    for (a1, a2, a3, pc), g in adm3_year.groupby(
        ["adm1_name", "adm2_name", "adm3_name", "adm3_src"]
    ):
        adm3.append(
            {
                "adm1": a1,
                "adm2": a2,
                "adm3": a3,
                "pcode": pc,
                "events": int(g["events"].sum()),
                "total": r6(g["people_affected"].sum()),
                "by_year": {int(y): r6(v) for y, v in zip(g["year"], g["people_affected"])},
            }
        )
    adm3.sort(key=lambda d: -d["total"])
    pts = ev.dropna(subset=["lat", "lon"])
    data = {
        "summary": {"r_crit": round(float(r_crit), 3)},
        "impact_monthly": records(
            monthly,
            {"year": "year", "month": "month", "events": "events", "people": "people_affected"},
        ),
        "comp_annual": records(
            comp_a,
            {
                "year": "year",
                "events": "events",
                "people": "people_affected",
                "households": "households_affected",
                "era5": "era5_annual_mm",
                "imerg": "imerg_annual_mm",
            },
        ),
        "adm1_totals": [{"adm1": k, "people": r6(v)} for k, v in adm1_tot.items()],
        "adm1_year": records(
            adm1_year, {"adm1": "adm1_name", "year": "year", "people": "people_affected"}
        ),
        "adm3": adm3,
        "rain_clim": records(clim, {"product": "product", "month": "month", "mm": "clim_mm"}),
        "rain_annual": records(rain_a, {"product": "product", "year": "year", "mm": "precip_mm"}),
        "trend_lines": trend_lines,
        "trends": records(
            trends,
            {
                "product": "product",
                "period": "period",
                "month": "month",
                "slope": "slope_mm_per_decade",
                "ci": "ci95_mm_per_decade",
                "p": "p_value",
            },
        ),
        "corr": [
            {
                "product": r["product"],
                "month": int(r["through_month"]),
                "n": int(r["n_years"]),
                "r": round(r["pearson_r"], 3),
                "p": round(r["p_value"], 3),
                "rho": round(r["spearman_rho"], 3),
            }
            for _, r in corr.iterrows()
        ],
        "monthly_corr": monthly_corr,
        "comp_monthly": records(
            comp_m,
            {
                "year": "year",
                "month": "month",
                "people": "people_affected",
                "era5": "era5_precip_mm",
                "imerg": "imerg_precip_mm",
                "era5_anom": "era5_anomaly_mm",
                "imerg_anom": "imerg_anomaly_mm",
            },
        ),
        "geo_adm3": geo3,
        "geo_adm1": geo1,
        "events_pts": [
            {
                "lat": round(r.lat, 4),
                "lon": round(r.lon, 4),
                "date": f"{r.date_alert:%d %b %Y}",
                "people": r6(r.people_affected),
                "commune": r.adm3_name,
                "localities": None
                if pd.isna(r.localities_source)
                else str(r.localities_source).strip(),
            }
            for r in pts.itertuples()
        ],
    }

    # --- embedded downloads ----------------------------------------------
    readme_path = PROC / "README_processed_data.txt"
    readme_path.write_text(readme(today.isoformat()), encoding="utf-8")
    files = [file_entry("raw", RAW / n, d) for n, d in RAW_FILES.items()]
    files.append(file_entry("processed", readme_path, "What each processed file contains"))
    files += [file_entry("processed", PROC / n, d) for n, d in PROCESSED_FILES.items()]

    hero = (SITE / "hero.js").read_text()
    product_dir = SITE / "impact-vs-rainfall"
    page = fill(
        (product_dir / "page.html").read_text(),
        values
        | {
            "CSS": (product_dir / "style.css").read_text(),
            "JS": (product_dir / "app.js").read_text(),
            "HERO_JS": hero,
            "DATA": embed_json(data),
            "FILES": embed_json(files),
        },
    )
    landing = fill(
        (SITE / "landing.html").read_text(),
        {k: values[k] for k in ["N_EVENTS", "N_PEOPLE", "BUILT_MONTH"]} | {"HERO_JS": hero},
    )

    (OUT / "impact-vs-rainfall").mkdir(parents=True, exist_ok=True)
    (OUT / "index.html").write_text(landing, encoding="utf-8")
    (OUT / "impact-vs-rainfall" / "index.html").write_text(page, encoding="utf-8")

    for k in [
        "PCT_JASO",
        "TOP2_TEXT",
        "R_ERA5",
        "R_IMERG",
        "RHO_ANOM_ERA5",
        "RHO_ANOM_IMERG",
        "TREND_ERA5",
        "TREND_IMERG",
        "ERA5_RANK_TEXT",
        "R_PRODUCTS",
        "R_CRIT",
    ]:
        print(f"{k}: {values[k]}")
    for p in [OUT / "index.html", OUT / "impact-vs-rainfall" / "index.html"]:
        print(f"{p.relative_to(ROOT)}: {p.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
