"""Build the raw + processed data behind the impact-vs-rainfall page.

Ports the logic of exploration/ocha_impact.ipynb into one reproducible pass:

- raw: the OCHA CAR flood compilation as received (blob), and national ERA5
  (monthly) and IMERG late v7 (daily) zonal means (prod DB public.era5 /
  public.imerg, pcode CF)
- processed: impact events matched to adm3 pcodes, monthly / annual / admin
  aggregates, rainfall totals, trends and anomalies for both products, the
  impact x rainfall comparison tables, and simplified boundaries

IMERG is not in the notebook: it is here as a robustness check on the ERA5
result, because ERA5 shows a steep drying trend over CAR that IMERG does not.

Everything is written under data/ (gitignored). Needs blob access and prod DB
read access; locally the DB is only reachable through the SSH tunnel, so set
DSCI_AZ_DB_PROD_HOST=127.0.0.1:15433 (see `db-tunnel up`).
"""

from io import BytesIO
from pathlib import Path

import numpy as np
import ocha_stratus as stratus
import pandas as pd
from scipy.stats import linregress, pearsonr, spearmanr

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

PRODUCTS = ["ERA5", "IMERG"]
CLIM_YEARS = (2001, 2020)  # shared baseline for monthly anomalies (IMERG starts 1998)
TREND_COMMON = (1998, 2025)  # full years both products cover
MIN_IMERG_DAYS = 25

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
# locality columns, reassigned by hand in the notebook (row index -> adm3).
ROW_OVERRIDES = {
    84: "mongoumba",
    223: "ouham fafa",
    88: "kaga-bandoro",
    89: "paoua",
    96: "arrondissement 1",
    127: "ouham fafa",
    115: "ouham fafa",
    107: "mbaiki",
    # Added for the site build: commune field contradicts the sous-prefecture,
    # locality and coordinates (caught by the prefecture check below).
    44: "mongoumba",  # Mongoumba / Zinga, commune "Mbaiki"
    87: "batangafo",  # Batangafo / Batangafo, commune "Mbaïki"
    97: "bimbo",  # Bangui-Fleuve / Begoua (the other Bégoua alerts are Bimbo), commune "Mbaïki"
    146: "samba-boungou",  # Bria / Bria centre, commune "Yéngou" (Yéngou is in Ippy, Ouaka)
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


def load_impact() -> tuple[pd.DataFrame, bytes]:
    raw = stratus.load_blob_data(IMPACT_BLOB)
    df = pd.read_excel(BytesIO(raw), sheet_name=IMPACT_SHEET)
    return df, raw


def match_adm3(df: pd.DataFrame, adm3: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    adm3 = adm3.copy()
    adm3["adm3_match"] = normalize(adm3["adm3_name"])

    norm = normalize(df["Commune"])
    df["adm3_match"] = norm.replace(NAME_FIXES)
    df["match_method"] = np.where(df["adm3_match"] != norm, "name_fix", "name")
    df.loc[list(ROW_OVERRIDES), "adm3_match"] = pd.Series(ROW_OVERRIDES)
    df.loc[list(ROW_OVERRIDES), "match_method"] = "row_override"

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


def main() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    PROC.mkdir(parents=True, exist_ok=True)

    # --- raw ---------------------------------------------------------------
    df_raw, raw_bytes = load_impact()
    (RAW / IMPACT_RAW_NAME).write_bytes(raw_bytes)

    era5 = load_era5()
    era5.to_csv(RAW / "era5_caf_adm0_monthly.csv", index=False)
    imerg = load_imerg()
    imerg.to_csv(RAW / "imerg_caf_adm0_daily.csv", index=False)

    adm3 = stratus.codab.load_codab_from_fieldmaps(iso3=ISO3.lower(), admin_level=3)
    adm1 = stratus.codab.load_codab_from_fieldmaps(iso3=ISO3.lower(), admin_level=1)

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

    # --- rainfall: ERA5 + IMERG monthly totals ----------------------------
    rain = pd.concat([era5_monthly_mm(era5), imerg_monthly_mm(imerg)], ignore_index=True)
    rain = rain.sort_values(["product", "year", "month"]).reset_index(drop=True)
    rain["cumul_mm"] = rain.groupby(["product", "year"])["precip_mm"].cumsum()
    clim = (
        rain[rain["year"].between(*CLIM_YEARS)]
        .groupby(["product", "month"])["precip_mm"]
        .mean()
        .rename("clim_mm")
    )
    rain = rain.join(clim, on=["product", "month"])
    rain["anomaly_mm"] = rain["precip_mm"] - rain["clim_mm"]
    rain.to_csv(PROC / "rain_monthly.csv", index=False)

    n_months = rain.groupby(["product", "year"])["month"].transform("count")
    rain_full = rain[n_months == 12]
    rain_annual = rain_full.groupby(["product", "year"])["precip_mm"].sum().reset_index()
    clim_annual = clim.groupby("product").sum()
    rain_annual["anomaly_pct"] = 100 * (
        rain_annual["precip_mm"] / rain_annual["product"].map(clim_annual) - 1
    )
    rain_annual.to_csv(PROC / "rain_annual.csv", index=False)

    # Linear trend per calendar month: over the common record (both products)
    # and over the full ERA5 record (what the notebook showed).
    trend_rows = []
    for product, (y0, y1) in [
        ("ERA5", TREND_COMMON),
        ("IMERG", TREND_COMMON),
        ("ERA5", (1981, 2025)),
    ]:
        for m in range(1, 13):
            sel = rain_full[
                (rain_full["product"] == product)
                & (rain_full["month"] == m)
                & rain_full["year"].between(y0, y1)
            ]
            res = linregress(sel["year"], sel["precip_mm"])
            trend_rows.append(
                {
                    "product": product,
                    "period": f"{y0}-{y1}",
                    "month": m,
                    "slope_mm_per_decade": 10 * res.slope,
                    "ci95_mm_per_decade": 10 * 1.96 * res.stderr,
                    "p_value": res.pvalue,
                }
            )
    for product, (y0, y1) in [
        ("ERA5", TREND_COMMON),
        ("IMERG", TREND_COMMON),
        ("ERA5", (1981, 2025)),
    ]:
        sel = rain_annual[(rain_annual["product"] == product) & rain_annual["year"].between(y0, y1)]
        res = linregress(sel["year"], sel["precip_mm"])
        trend_rows.append(
            {
                "product": product,
                "period": f"{y0}-{y1}",
                "month": 0,
                "slope_mm_per_decade": 10 * res.slope,
                "ci95_mm_per_decade": 10 * 1.96 * res.stderr,
                "p_value": res.pvalue,
            }
        )
    trends = pd.DataFrame(trend_rows)
    trends.to_csv(PROC / "rain_trends.csv", index=False)

    # --- impact x rainfall -------------------------------------------------
    wide_annual = rain_annual.pivot(index="year", columns="product", values="precip_mm")
    wide_annual.columns = [f"{c.lower()}_annual_mm" for c in wide_annual.columns]
    comp_annual = annual.reset_index().join(wide_annual, on="year")
    comp_annual.to_csv(PROC / "impact_vs_rain_annual.csv", index=False)

    corr_rows = []
    for product in PRODUCTS:
        cum = rain[rain["product"] == product].pivot(
            index="year", columns="month", values="cumul_mm"
        )
        for m in range(1, 13):
            x = cum.loc[comp_annual["year"], m].to_numpy()
            y = comp_annual["people_affected"].to_numpy()
            r, p = pearsonr(x, y)
            corr_rows.append(
                {
                    "product": product,
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
        index=["year", "month"], columns="product", values=["precip_mm", "anomaly_mm"]
    )
    wide_monthly.columns = [f"{prod.lower()}_{var}" for var, prod in wide_monthly.columns]
    comp_monthly = monthly.join(wide_monthly, on=["year", "month"])
    comp_monthly.to_csv(PROC / "impact_vs_rain_monthly.csv", index=False)

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
    print(corr[corr["through_month"].isin([9, 11, 12])].round(2).to_string(index=False))
    for product in PRODUCTS:
        c = comp_monthly.dropna(subset=[f"{product.lower()}_precip_mm"])
        for var in ["precip_mm", "anomaly_mm"]:
            x = c[f"{product.lower()}_{var}"]
            print(
                f"monthly {product} {var} vs people (n={len(c)}): "
                f"pearson {pearsonr(x, c['people_affected'])[0]:.2f}, "
                f"spearman {spearmanr(x, c['people_affected'])[0]:.2f}"
            )


if __name__ == "__main__":
    main()
