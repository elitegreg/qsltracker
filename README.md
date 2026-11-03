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

## Tests

```sh
uv run pytest
```
