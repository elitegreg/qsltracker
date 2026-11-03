# qsltracker

Amateur radio QSL card tracker with a Textual TUI.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/). From the project root:

```sh
uv sync
```

## Usage

```sh
uv run qsltracker --help
uv run qsltracker --config ~/.config/qsltracker/config.yaml
uv run qsltracker --qsos-per-label 3
```

## Configuration

`config.yaml` fields:

- `qsos_per_label` (int, default `5`)
- `my_gridsquare`, `my_cq_zone`, `my_itu_zone`, `my_cnty` (station defaults)
- `qrz_username`, `qrz_password` (QRZ credentials)

There is intentionally no `my_state` setting; it is derived from
`my_cnty` during ADIF import.

## Files

Everything lives next to the config file (`~/.config/qsltracker/` by
default):

- `qsos.db` — the SQLite database.
- `qsltracker.log` — QSL field changes and `QSL_VIA` parse notes
  (ADIF imports are not logged).
- `all-sent.csv` — regenerated on every label export, listing all
  QSOs with `QSL_SENT='Y'` across the database.

## Tests

```sh
uv run pytest
```

QRZ records are cached only in memory, using a separate
`functools.lru_cache(maxsize=1024)` for each update or export operation.
No QRZ SQLite cache is opened or created; existing QRZ cache files are unused.

Exports validate current batch routing and status. Queued and previously sent
QSOs may be exported; reprinting preserves existing sent dates. Address lookup
and CSV staging finish before existing output files are replaced. Runtime
publication failures restore previous outputs; replacement of multiple files
is not atomic across a process crash.
