"""Build the raw + processed data behind the impact-vs-rainfall page.

Ports the logic of exploration/ocha_impact.ipynb into one reproducible pass:

- raw: the OCHA CAR flood compilation as received (blob); national ERA5
  (monthly) and IMERG late v7 (daily) zonal means (prod DB public.era5 /
  public.imerg, pcode CF); and CHIRPS v3 monthly national means, read
  window-only from the UCSB cloud-optimised GeoTIFFs (the team has no CHIRPS
  observation pipeline) and cached in data/raw
- processed: impact events matched to adm3 pcodes, monthly / annual / admin
  aggregates, rainfall totals, trends, anomalies and detrended anomalies for
  the three products, the impact x rainfall comparison tables, and simplified
  boundaries

IMERG and CHIRPS are not in the notebook: they are here as robustness checks on
the ERA5 result, because ERA5 shows a steep drying trend over CAR that the
other two do not. Detrending (per calendar month, linear, 1998-2025) removes
that trend before comparing rainfall with impact.

Everything is written under data/ (gitignored). The raw extracts are cached in
data/raw and reused; pass --refresh to re-pull the workbook (blob) and the ERA5
/ IMERG extracts (prod DB; locally only reachable through the SSH tunnel, so set
DSCI_AZ_DB_PROD_HOST=127.0.0.1:15433, see `db-tunnel up`). CHIRPS is always
topped up with any new months.
"""

import argparse
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import from_bounds
from scipy.stats import linregress, pearsonr, spearmanr, t

from src.constants import ISO3, PROJECT_PREFIX

ROOT = Path(__file__).parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"

IMPACT_BLOB = f"{PROJECT_PREFIX}/raw/ocha/OCHA CAR_DONNEES-INONDATIONS_DATA_COMPIL_2023OLDOK.xlsx"
IMPACT_SHEET = "DATA FOR PBI"
IMPACT_RAW_NAME = "OCHA_CAR_donnees_inondations_compil_2021-2025.xlsx"

# The compilation covers alerts from Apr 2021 to Nov 2025; months inside these
# years with no alert are treated as zero people affected.
IMPACT_YEARS = range(2021, 2026)

PRODUCTS = ["ERA5", "IMERG", "CHIRPS"]
CLIM_YEARS = (2001, 2020)  # shared baseline for monthly anomalies (IMERG starts 1998)
TREND_COMMON = (1998, 2025)  # full years all three products cover; also the detrending fit
TREND_FULL = (1981, 2025)  # full ERA5 / CHIRPS record
MIN_IMERG_DAYS = 25

CHIRPS_DIR = "https://data.chc.ucsb.edu/products/CHIRPS/v3.0/monthly/global/cogs/"
CHIRPS_RAW_NAME = "chirps_v3_caf_adm0_monthly.csv"

# Commune spellings in the compilation -> FieldMaps adm3 names (normalised).
NAME_FIXES = {
    "1er arrondissement": "arrondissement 1",
    "bria": "samba-boungou",
    "mbaïki": "mbaiki",
    "dar'el-kouti": "dar el kouti",
    "boromata": "ouandja",
    "moyenne sido": "sido",
    "ouandago": "kaga-bandoro",
    "9e arrondissement": "bimbo",
}
NAME_FIXES.update({f"{x}e arrondissement": f"arrondissement {x}" for x in range(9)})

# Rows whose commune is missing or inconsistent with the sous-prefecture /
# locality columns, reassigned by hand: row index -> (expected sous-prefecture,
# expected alert date, adm3). The expected values guard against the sheet being
# re-sorted upstream, which would otherwise move an override onto another alert.
ROW_OVERRIDES = {
    # from the notebook
    84: ("Mongoumba", "2022-10-24", "mongoumba"),
    223: ("Bouca", "2025-10-17", "ouham fafa"),
    88: ("Kaga-Bandoro", "2022-09-30", "kaga-bandoro"),
    89: ("Paoua", "2022-09-30", "paoua"),
    96: ("Bangui-Fleuve", "2023-11-25", "arrondissement 1"),
    127: ("Kabo", "2023-09-07", "ouham fafa"),
    115: ("Bouca", "2023-05-06", "ouham fafa"),
    107: ("Mbaïki", "2023-03-22", "mbaiki"),
    # added for the site build: the commune field contradicts the sous-prefecture,
    # locality and coordinates (caught by check_prefectures below)
    44: ("Mongoumba", "2022-07-22", "mongoumba"),  # locality Zinga, commune "Mbaiki"
    87: ("Batangafo", "2022-11-15", "batangafo"),  # locality Batangafo, commune "Mbaïki"
    97: ("Bangui-Fleuve", "2023-11-25", "bimbo"),  # locality Begoua (other Bégoua alerts: Bimbo)
    146: ("Bria", "2024-04-21", "samba-boungou"),  # Bria centre; Yéngou is in Ippy, Ouaka
}

# Prefectures created in the 2020-21 reform -> the COD (FieldMaps) prefecture that
# contains them; used only to sanity-check the adm3 matches.
PREFECTURE_PARENT = {
    "ouham-fafa": "ouham",
    "lim-pendé": "ouham pendé",
    "mambéré": "mambéré-kadéï",
}

IMPACT_COLS = {
    "Individus affecté": "people_affected",
    "Ménage affecté": "households_affected",
    "Infrastructures privées écroulées (maisons/ boutiques)": "houses_collapsed",
    "Infrastructures privées à toitures emportées (maisons/ boutiques)": "houses_roof_lost",
    "Infrastructures privées (maisons/boutiques) Affectées": "houses_affected",
    "Perte en vie humaine": "deaths",
    "Blessures": "injured",
    "Superficie cultivable affectée (ha)": "cropland_affected_ha",
}


def normalize(s: pd.Series) -> pd.Series:
    return s.astype(str).str.strip().str.lower().str.replace(r"\s+", " ", regex=True)


def load_impact() -> bytes:
    return stratus.load_blob_data(IMPACT_BLOB)


def match_adm3(df: pd.DataFrame, adm3: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    adm3 = adm3.copy()
    adm3["adm3_match"] = normalize(adm3["adm3_name"])

    norm = normalize(df["Commune"])
    df["adm3_match"] = norm.replace(NAME_FIXES)
    df["match_method"] = np.where(df["adm3_match"] != norm, "name_fix", "name")
    for i, (sous_pref, day, target) in ROW_OVERRIDES.items():
        row = df.loc[i]
        if (
            row["Sous-préfecture"] != sous_pref
            or str(pd.Timestamp(row["Date alerte"]).date()) != day
        ):
            raise ValueError(f"override row {i} is no longer {sous_pref} {day}: sheet changed?")
        df.loc[i, ["adm3_match", "match_method"]] = [target, "row_override"]

    # adm3 names are not unique (two "Nola"); only match on names that are,
    # and fail loudly if an impact row lands on an ambiguous or unknown one.
    counts = adm3["adm3_match"].value_counts()
    ambiguous = set(counts[counts > 1].index)
    hit = df["adm3_match"].isin(ambiguous)
    if hit.any():
        raise ValueError(
            f"impact rows on ambiguous adm3 names: {df.loc[hit, 'adm3_match'].unique()}"
        )

    keep = ["adm3_match", "adm3_src", "adm3_name", "adm2_src", "adm2_name", "adm1_src", "adm1_name"]
    lookup = adm3.loc[~adm3["adm3_match"].isin(ambiguous), keep]
    out = df.merge(lookup, on="adm3_match", how="left", validate="m:1")
    missing = out["adm3_src"].isna()
    if missing.any():
        raise ValueError(f"unmatched communes: {out.loc[missing, 'Commune'].unique()}")
    check_prefectures(out)
    return out.drop(columns="adm3_match")


def check_prefectures(df: pd.DataFrame) -> None:
    """Print alerts whose matched adm1 disagrees with the source prefecture.

    Expected disagreements: Bimbo (filed under Bangui, COD puts it in Ombella
    M'Poko), the hand overrides for mis-filed communes, and data-entry noise in
    the prefecture column itself. Anything else needs a look.
    """

    def key(s: pd.Series) -> pd.Series:
        s = normalize(s).str.replace("'", "").str.replace("-", " ")
        return s.str.replace(r"\s+", " ", regex=True)

    parent = {k.replace("-", " "): v for k, v in PREFECTURE_PARENT.items()}
    src = key(df["Préfecture"]).replace(parent).str.replace("-", " ")
    cod = key(df["adm1_name"])
    bad = df[(src != cod) & ~((df["adm3_name"] == "Bimbo") & (src == "bangui"))]
    if len(bad):
        print(f"{len(bad)} alerts whose adm3 is outside the source prefecture:")
        print(
            bad[
                [
                    "Préfecture",
                    "Sous-préfecture",
                    "Commune",
                    "Localité(s)",
                    "adm1_name",
                    "adm3_name",
                    "match_method",
                ]
            ].to_string()
        )


def load_era5() -> pd.DataFrame:
    query = "SELECT * FROM public.era5 WHERE pcode = 'CF' ORDER BY valid_date"
    with stratus.get_engine(stage="prod").connect() as conn:
        return pd.read_sql(query, conn, parse_dates=["valid_date"])


def load_imerg() -> pd.DataFrame:
    query = "SELECT * FROM public.imerg WHERE pcode = 'CF' AND adm_level = 0 ORDER BY valid_date"
    with stratus.get_engine(stage="prod").connect() as conn:
        return pd.read_sql(query, conn, parse_dates=["valid_date"])


CHIRPS_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_TIMEOUT="30",
    GDAL_HTTP_CONNECTTIMEOUT="15",
    GDAL_HTTP_MAX_RETRY="3",
    GDAL_HTTP_RETRY_DELAY="2",
)


def chirps_month(month: pd.Timestamp, geom) -> dict:
    """CHIRPS v3 national mean for one month (mm), reading only the CAR window."""
    url = f"/vsicurl/{CHIRPS_DIR}chirps-v3.0.{month.year}.{month.month:02d}.cog"
    minx, miny, maxx, maxy = geom.bounds
    with rasterio.Env(**CHIRPS_ENV), rasterio.open(url) as src:
        win = from_bounds(minx - 0.1, miny - 0.1, maxx + 0.1, maxy + 0.1, src.transform)
        win = win.round_offsets().round_lengths()
        a = src.read(1, window=win)
        inside = geometry_mask(
            [geom], out_shape=a.shape, transform=src.window_transform(win), invert=True
        )
    vals = a[inside]
    if not np.isfinite(vals).all() or (vals < 0).any():
        raise ValueError(f"CHIRPS no-data inside CAR in {month:%Y-%m}")
    return {"valid_date": month, "mean": float(vals.mean()), "n_pixels": int(vals.size)}


def load_chirps(geom) -> pd.DataFrame:
    """National mean CHIRPS v3 monthly totals (mm), 1981 to the latest month.

    Cached in data/raw and resumable: each month is appended as it arrives and
    only months missing from the cache are fetched (delete the file to refetch
    everything, e.g. after a CHIRPS revision).
    """
    cache = RAW / CHIRPS_RAW_NAME
    have = set()
    if cache.exists():
        # drop rows a killed run may have left half-written, so they are refetched
        old = pd.read_csv(cache, parse_dates=["valid_date"])
        ok = np.isfinite(old["mean"]) & np.isfinite(old["n_pixels"])
        old[ok].to_csv(cache, index=False)
        have = set(old.loc[ok, "valid_date"])
    with urllib.request.urlopen(CHIRPS_DIR, timeout=60) as r:
        listing = r.read().decode()
    found = re.findall(r"chirps-v3\.0\.(\d{4})\.(\d{2})\.cog", listing)
    assert found, "CHIRPS directory listing has no monthly COGs (format changed?)"
    todo = sorted({pd.Timestamp(int(y), int(m), 1) for y, m in found} - have)
    if todo:
        print(f"fetching {len(todo)} CHIRPS months", flush=True)
        if not cache.exists():
            cache.write_text("valid_date,mean,n_pixels\n")
        failed = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            futures = {ex.submit(chirps_month, m, geom): m for m in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                try:
                    row = fut.result()
                except Exception as e:  # keep the rest; report and fail below
                    failed.append((futures[fut], e))
                    continue
                with cache.open("a") as f:
                    f.write(f"{row['valid_date']:%Y-%m-%d},{row['mean']},{row['n_pixels']}\n")
                if i % 50 == 0:
                    print(f"  {i}/{len(todo)}", flush=True)
        if failed:
            raise RuntimeError(
                f"{len(failed)} CHIRPS months failed (re-run to resume): {failed[:3]}"
            )
    out = pd.read_csv(cache, parse_dates=["valid_date"]).drop_duplicates("valid_date")
    out = out.sort_values("valid_date").reset_index(drop=True)
    # every month is masked on the same grid; a different count means CHIRPS changed
    assert out["n_pixels"].nunique() == 1, out["n_pixels"].value_counts()
    out.to_csv(cache, index=False)
    return out


def chirps_monthly_mm(chirps: pd.DataFrame) -> pd.DataFrame:
    d = chirps["valid_date"]
    return pd.DataFrame(
        {"product": "CHIRPS", "year": d.dt.year, "month": d.dt.month, "precip_mm": chirps["mean"]}
    )


def era5_monthly_mm(era5: pd.DataFrame) -> pd.DataFrame:
    """ERA5 stores the monthly mean daily rate (mm/day); convert to monthly totals."""
    d = era5["valid_date"]
    return pd.DataFrame(
        {
            "product": "ERA5",
            "year": d.dt.year,
            "month": d.dt.month,
            "precip_mm": era5["mean"] * d.dt.days_in_month,
        }
    )


def imerg_monthly_mm(imerg: pd.DataFrame) -> pd.DataFrame:
    """IMERG is daily (mm/day); scale the mean of available days to the month.

    Months with fewer than MIN_IMERG_DAYS days are dropped (incomplete).
    """
    d = imerg["valid_date"]
    g = imerg.assign(year=d.dt.year, month=d.dt.month).groupby(["year", "month"])["mean"]
    out = g.agg(["mean", "count"]).reset_index()
    first = pd.to_datetime(dict(year=out["year"], month=out["month"], day=1))
    out = out[out["count"] >= MIN_IMERG_DAYS]
    return pd.DataFrame(
        {
            "product": "IMERG",
            "year": out["year"],
            "month": out["month"],
            "precip_mm": out["mean"] * first.loc[out.index].dt.days_in_month,
        }
    )


def rainfall_tables(era5, imerg, chirps):
    """Monthly/annual rainfall per product with anomalies, detrended values and trends."""
    rain = pd.concat(
        [era5_monthly_mm(era5), imerg_monthly_mm(imerg), chirps_monthly_mm(chirps)],
        ignore_index=True,
    )
    rain = rain.sort_values(["product", "year", "month"]).reset_index(drop=True)
    # a dropped month would make the cumulative totals silently too low
    for product in PRODUCTS:
        got = rain[(rain["product"] == product) & rain["year"].isin(IMPACT_YEARS)]
        assert len(got) == 12 * len(IMPACT_YEARS), f"{product} is missing months in {IMPACT_YEARS}"
    rain["full_year"] = rain.groupby(["product", "year"])["month"].transform("count") == 12
    rain["cumul_mm"] = rain.groupby(["product", "year"])["precip_mm"].cumsum()
    clim = (
        rain[rain["year"].between(*CLIM_YEARS)]
        .groupby(["product", "month"])["precip_mm"]
        .mean()
        .rename("clim_mm")
    )
    rain = rain.join(clim, on=["product", "month"])
    rain["anomaly_mm"] = rain["precip_mm"] - rain["clim_mm"]

    # Detrended anomaly: residual from a linear trend fitted per product and
    # calendar month over TREND_COMMON. OLS fits on the same years are additive,
    # so cumulative and annual detrended values are sums of the monthly ones.
    fit_rows = []
    in_fit = rain["full_year"] & rain["year"].between(*TREND_COMMON)
    for (product, m), g in rain[in_fit].groupby(["product", "month"]):
        res = linregress(g["year"], g["precip_mm"])
        fit_rows.append({"product": product, "month": m, "_a": res.intercept, "_b": res.slope})
    rain = rain.merge(pd.DataFrame(fit_rows), on=["product", "month"], how="left")
    rain["trend_mm"] = rain["_a"] + rain["_b"] * rain["year"]
    # residuals only inside the fit window: elsewhere they would be extrapolations
    in_window = rain["full_year"] & rain["year"].between(*TREND_COMMON)
    rain["detrended_mm"] = (rain["precip_mm"] - rain["trend_mm"]).where(in_window)
    rain["cumul_detrended_mm"] = rain.groupby(["product", "year"])["detrended_mm"].cumsum()
    rain = rain.drop(columns=["_a", "_b"])
    rain.to_csv(PROC / "rain_monthly.csv", index=False)

    rain_full = rain[rain["full_year"]]
    rain_annual = (
        rain_full.groupby(["product", "year"])[["precip_mm", "detrended_mm"]]
        .sum(min_count=12)
        .reset_index()
    )
    clim_annual = clim.groupby("product").sum()
    rain_annual["anomaly_pct"] = 100 * (
        rain_annual["precip_mm"] / rain_annual["product"].map(clim_annual) - 1
    )
    rain_annual.to_csv(PROC / "rain_annual.csv", index=False)

    # Linear trend per calendar month (and annual, month 0): over the record all
    # three products share, and over the full ERA5 / CHIRPS record.
    trend_rows = []
    periods = [(p, TREND_COMMON) for p in PRODUCTS] + [(p, TREND_FULL) for p in ["ERA5", "CHIRPS"]]
    for product, (y0, y1) in periods:
        for m in range(0, 13):
            sel = (
                rain_annual[(rain_annual["product"] == product)]
                if m == 0
                else rain_full[(rain_full["product"] == product) & (rain_full["month"] == m)]
            )
            sel = sel[sel["year"].between(y0, y1)]
            res = linregress(sel["year"], sel["precip_mm"])
            trend_rows.append(
                {
                    "product": product,
                    "period": f"{y0}-{y1}",
                    "month": m,
                    "slope_mm_per_decade": 10 * res.slope,
                    "ci95_mm_per_decade": 10 * t.ppf(0.975, len(sel) - 2) * res.stderr,
                    "p_value": res.pvalue,
                }
            )
    trends = pd.DataFrame(trend_rows)
    trends.to_csv(PROC / "rain_trends.csv", index=False)
    return rain, rain_annual, trends


def impact_vs_rain(annual, monthly, rain, rain_annual):
    """Join impact to rainfall: annual table, cumulative correlations, monthly table."""
    wide_annual = rain_annual.pivot(
        index="year", columns="product", values=["precip_mm", "detrended_mm"]
    )
    wide_annual.columns = [
        f"{p.lower()}_annual_mm" if v == "precip_mm" else f"{p.lower()}_annual_detrended_mm"
        for v, p in wide_annual.columns
    ]
    comp_annual = annual.reset_index().join(wide_annual, on="year")
    comp_annual.to_csv(PROC / "impact_vs_rain_annual.csv", index=False)

    corr_rows = []
    y = comp_annual["people_affected"].to_numpy()
    for product in PRODUCTS:
        for basis, col in [("raw", "cumul_mm"), ("detrended", "cumul_detrended_mm")]:
            cum = rain[rain["product"] == product].pivot(index="year", columns="month", values=col)
            for m in range(1, 13):
                x = cum.loc[comp_annual["year"], m].to_numpy()
                r, p = pearsonr(x, y)
                corr_rows.append(
                    {
                        "product": product,
                        "basis": basis,
                        "through_month": m,
                        "n_years": len(y),
                        "pearson_r": r,
                        "p_value": p,
                        "spearman_rho": spearmanr(x, y)[0],
                    }
                )
    corr = pd.DataFrame(corr_rows)
    corr.to_csv(PROC / "cumulative_rain_correlation.csv", index=False)

    wide_monthly = rain.pivot_table(
        index=["year", "month"],
        columns="product",
        values=["precip_mm", "anomaly_mm", "detrended_mm"],
    )
    wide_monthly.columns = [f"{prod.lower()}_{var}" for var, prod in wide_monthly.columns]
    comp_monthly = monthly.join(wide_monthly, on=["year", "month"])
    comp_monthly.to_csv(PROC / "impact_vs_rain_monthly.csv", index=False)
    return comp_annual, corr, comp_monthly


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="re-pull the blob and DB extracts")
    refresh = ap.parse_args().refresh
    RAW.mkdir(parents=True, exist_ok=True)
    PROC.mkdir(parents=True, exist_ok=True)

    # --- raw (cached in data/raw unless --refresh) ---------------------------
    impact_path = RAW / IMPACT_RAW_NAME
    if refresh or not impact_path.exists():
        impact_path.write_bytes(load_impact())
    df_raw = pd.read_excel(impact_path, sheet_name=IMPACT_SHEET)

    era5_path, imerg_path = RAW / "era5_caf_adm0_monthly.csv", RAW / "imerg_caf_adm0_daily.csv"
    if refresh or not era5_path.exists():
        load_era5().to_csv(era5_path, index=False)
    if refresh or not imerg_path.exists():
        load_imerg().to_csv(imerg_path, index=False)
    era5 = pd.read_csv(era5_path, parse_dates=["valid_date"])
    imerg = pd.read_csv(imerg_path, parse_dates=["valid_date"])

    adm3 = stratus.codab.load_codab_from_fieldmaps(iso3=ISO3.lower(), admin_level=3)
    adm1 = stratus.codab.load_codab_from_fieldmaps(iso3=ISO3.lower(), admin_level=1)
    chirps = load_chirps(adm1.geometry.union_all())

    # --- impact events -----------------------------------------------------
    ev = match_adm3(df_raw, adm3)
    ev["date_alert"] = pd.to_datetime(ev["Date alerte"])
    ev["year"] = ev["date_alert"].dt.year
    ev["month"] = ev["date_alert"].dt.month
    assert (ev["year"] == ev["YEAR"]).all(), "YEAR column disagrees with Date alerte"
    ev = ev.rename(columns=IMPACT_COLS)
    for c in IMPACT_COLS.values():
        ev[c] = pd.to_numeric(ev[c], errors="coerce")

    events = ev[
        [
            "date_alert",
            "year",
            "month",
            "Préfecture",
            "Sous-préfecture",
            "Commune",
            "Localité(s)",
            "adm1_src",
            "adm1_name",
            "adm2_src",
            "adm2_name",
            "adm3_src",
            "adm3_name",
            "match_method",
            "Longitude",
            "Latitude",
            "Source(s)",
            "ZONE",
            *IMPACT_COLS.values(),
        ]
    ].rename(
        columns={
            "Préfecture": "prefecture_source",
            "Sous-préfecture": "sous_prefecture_source",
            "Commune": "commune_source",
            "Localité(s)": "localities_source",
            "Longitude": "lon",
            "Latitude": "lat",
            "Source(s)": "source",
            "ZONE": "ocha_zone",
        }
    )
    events = events.sort_values("date_alert").reset_index(drop=True)
    events.to_csv(PROC / "impact_events_adm3.csv", index=False)

    # --- impact aggregates -------------------------------------------------
    assert events["year"].isin(IMPACT_YEARS).all(), "alerts outside the expected years"
    grid = pd.MultiIndex.from_product([IMPACT_YEARS, range(1, 13)], names=["year", "month"])
    monthly = (
        events.groupby(["year", "month"])
        .agg(
            events=("people_affected", "size"),
            people_affected=("people_affected", "sum"),
            households_affected=("households_affected", "sum"),
        )
        .reindex(grid, fill_value=0)
        .reset_index()
    )
    monthly.to_csv(PROC / "impact_monthly.csv", index=False)

    annual = monthly.groupby("year")[["events", "people_affected", "households_affected"]].sum()

    by_adm3 = (
        events.groupby(["adm1_name", "adm2_name", "adm3_name", "adm3_src", "year"])
        .agg(events=("people_affected", "size"), people_affected=("people_affected", "sum"))
        .reset_index()
    )
    by_adm3.to_csv(PROC / "impact_adm3_by_year.csv", index=False)

    by_adm1 = (
        events.groupby(["adm1_name", "adm1_src", "year"])
        .agg(events=("people_affected", "size"), people_affected=("people_affected", "sum"))
        .reset_index()
    )
    by_adm1.to_csv(PROC / "impact_adm1_by_year.csv", index=False)

    # --- rainfall, and impact x rainfall ------------------------------------
    rain, rain_annual, trends = rainfall_tables(era5, imerg, chirps)
    comp_annual, corr, comp_monthly = impact_vs_rain(annual, monthly, rain, rain_annual)

    # --- boundaries for the map (simplified, ~100 m) -------------------------
    totals3 = by_adm3.groupby("adm3_src")[["events", "people_affected"]].sum()
    g3 = adm3[["adm3_src", "adm3_name", "adm2_name", "adm1_name", "geometry"]].copy()
    g3 = g3.join(totals3, on="adm3_src")
    g3["geometry"] = g3.geometry.simplify(0.01, preserve_topology=True)
    g3.to_file(PROC / "adm3_impact.geojson", driver="GeoJSON", COORDINATE_PRECISION=3)

    totals1 = by_adm1.groupby("adm1_src")[["events", "people_affected"]].sum()
    g1 = adm1[["adm1_src", "adm1_name", "geometry"]].copy().join(totals1, on="adm1_src")
    g1["geometry"] = g1.geometry.simplify(0.01, preserve_topology=True)
    g1.to_file(PROC / "adm1_impact.geojson", driver="GeoJSON", COORDINATE_PRECISION=3)

    # --- console summary -----------------------------------------------------
    print(f"events: {len(events)}  people affected: {events['people_affected'].sum():,.0f}")
    print(f"match methods: {events['match_method'].value_counts().to_dict()}")
    print(comp_annual.round(0).to_string(index=False))
    print(trends[trends["month"] == 0].round(2).to_string(index=False))
    sel = corr[corr["through_month"].isin([9, 12])]
    print(sel.round(2).to_string(index=False))
    for product in PRODUCTS:
        for var in ["precip_mm", "anomaly_mm", "detrended_mm"]:
            x = comp_monthly[f"{product.lower()}_{var}"]
            yy = comp_monthly["people_affected"]
            print(
                f"monthly {product} {var} vs people (n={len(x)}): "
                f"pearson {pearsonr(x, yy)[0]:.2f}, spearman {spearmanr(x, yy)[0]:.2f}"
            )


if __name__ == "__main__":
    main()
