# Central African Republic floods analysis

Analysis for floods in Central African Republic. Not currently for an anticipatory action framework.

## Site: flood impact vs rainfall

A password-protected GitHub Pages site (landing page at the root, the analysis under
`/impact-vs-rainfall/`) built from `exploration/ocha_impact.ipynb`, plus robustness checks
the notebook doesn't have: IMERG and CHIRPS v3 alongside ERA5, and detrended rainfall
(ERA5 has a spurious drying trend over CAR). The OCHA impact compilation includes 2025
alerts that are not yet on HDX, so every page is encrypted with
[staticrypt](https://github.com/robinmoisson/staticrypt) and the downloads are embedded
inside the encrypted HTML.

```sh
uv run python scripts/build_data.py   # -> data/ (gitignored), from the cached raw extracts
# first run, or to re-pull the workbook and the ERA5/IMERG extracts:
db-tunnel up   # prod DB is only reachable through the SSH tunnel
DSCI_AZ_DB_PROD_HOST=127.0.0.1:15433 uv run python scripts/build_data.py --refresh
SITE_PASSWORD=... scripts/publish.sh   # build_site.py -> staticrypt -> gh-pages
```

- `scripts/build_data.py`: raw files (impact workbook from blob; ERA5 + IMERG national
  zonal stats from the prod DB; CHIRPS v3 monthly national means read window-only from
  the UCSB cloud-optimised GeoTIFFs, resumable, topped up on every run) and processed
  tables under `data/`
- `scripts/build_site.py`: renders `site_build/` from `site/` templates with the data
  and the downloadable files inlined (never committed or served)
- `scripts/publish.sh`: encrypts each page and pushes exactly `index.html`,
  `impact-vs-rainfall/index.html` and `.nojekyll` to `gh-pages` (asserted before and
  after the push). The password is passed in the environment, never stored here.
