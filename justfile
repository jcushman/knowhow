set shell := ["bash", "-cu"]

event := "events/bread-class.yaml"
sim := "sims/bread-class.yaml"

# List recipes.
default:
    @just --list

# Run the offline test suite (no model calls).
test:
    uv run pytest -q

# Run the whole simulation pipeline offline with the fake model; writes runs/dry-<time>.
sim-dry minutes="4":
    uv run knowhow sim {{event}} {{sim}} --dry-run --minutes {{minutes}} --out runs/dry-$(date +%Y%m%d-%H%M%S)

# Run a short real simulation (costs money). Usage: just sim-mini openrouter:<slug>
sim-mini model minutes="6" groups="A-2291,C-4817":
    uv run python -u -m knowhow.cli sim {{event}} {{sim}} --model {{model}} --minutes {{minutes}} --groups {{groups}} --out runs/mini-$(date +%Y%m%d-%H%M%S)

# Rebuild walkthrough.html for a finished run. Usage: just walkthrough runs/<name>
walkthrough run:
    uv run knowhow walkthrough {{event}} {{sim}} {{run}}

# Serve a local session with the fake model and typed input only, at http://localhost:8000.
serve-dry:
    uv run knowhow serve {{event}} --data sessions/dry --asr none --dry-run --admin-key dev

# Regenerate the ADR index from the records under docs/adr/.
docs-adr-index:
    python3 scripts/build_adr_index.py

# Create and index a proposed ADR. Usage: just adr "Decision title"
adr title:
    python3 scripts/new_adr.py {{quote(title)}}
