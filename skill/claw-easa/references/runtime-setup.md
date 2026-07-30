# claw-easa runtime setup

The OpenClaw skill package only provides agent instructions. The local machine must also have
this repository's Python runtime installed so that the `claw-easa` CLI exists.

## Recommended setup from GitHub

The bootstrap installs a CPU-only torch build by default so that normal OpenClaw hosts do not pull large CUDA wheels unnecessarily.

```bash
git clone https://github.com/dmoraine/clawEASA.git
cd clawEASA
./scripts/bootstrap-local-runtime.sh
./scripts/install-openclaw-skill.sh
./scripts/check-openclaw-runtime.sh
```

## Minimum checks

```bash
. .venv/bin/activate
python -m claw_easa.cli --help
python -m claw_easa.cli status
```

## First real ingestion test

```bash
. .venv/bin/activate
python -m claw_easa.cli init
python -m claw_easa.cli ingest fetch air-ops
python -m claw_easa.cli ingest parse air-ops
python -m claw_easa.cli lookup ORO.FTL.110
```

The normal `ingest fetch` path currently works and should be tried first.
EASA has returned a JavaScript bot-challenge in the past and may still do so
conditionally. If the CLI reports a challenge, retry with the headless
browser backend:

```bash
pip install 'claw-easa[browser]' && playwright install chromium
python -m claw_easa.cli ingest fetch air-ops --browser
python -m claw_easa.cli ingest parse air-ops
```

Alternatively, download the ZIP/XML via a browser and ingest the local file:
`python -m claw_easa.cli ingest parse air-ops --file <path>`. See
`usage.md` → "Download behavior and challenge fallbacks" for all options.

If `claw-easa` is not available yet, do not pretend the skill is fully installed.
Bootstrap the runtime first.
