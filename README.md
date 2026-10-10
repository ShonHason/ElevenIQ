# ElevenIQ

A data-driven football scouting platform: player search, statistical "Player DNA" profiles,
similar players and compare mode, built on StatsBomb Open Data (2015/16). See `docs/brief.md`
for the project and `CLAUDE.md` for how it is built.

## Run locally

Prerequisites: Python 3.11, Node 22 (`frontend/.nvmrc`), and the processed data under `data/`
(the pipeline below writes it; `data/` is never committed).

```bash
# once: the Python environment and the frontend packages
python3.11 -m venv .venv && .venv/bin/pip install -e ".[dev]"
(cd frontend && npm install && npx playwright install chromium)

# once (or after a data change): download StatsBomb open data and build the pipeline
.venv/bin/python -m eleveniq.statsbomb.downloader
.venv/bin/python -m eleveniq.pipeline.build_player_match
.venv/bin/python -m eleveniq.pipeline.build_player_season
.venv/bin/python -m eleveniq.pipeline.build_percentiles   # refuses unless the season parquet is the one
                                                          # the committed measurements were made on
.venv/bin/python -m eleveniq.pipeline.build_player_dna

# the site: builds the web export if it is missing or stale, then starts the dev server
cd frontend && npm run local
```

Then open the URL it prints (http://localhost:5173/, or the next free port).

- `npm run local` serves the **full export** (`export/v1/`, every player). It rebuilds the export
  when a parquet it was built from changed or the exporter code is newer.
- Opening `frontend/index.html` straight from disk does not work (the browser blocks the data
  over `file://`); the page says so.
- Tests run on the small committed fixture (`tests/fixtures/web/`, 29 players): `npm test`
  (Vitest) and `npm run e2e` (Playwright; one smoke test also runs on the full export when it
  exists). Python tests: `.venv/bin/python -m pytest tests/ -q -m "not integration"`.
