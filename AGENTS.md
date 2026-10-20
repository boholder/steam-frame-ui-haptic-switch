# AGENTS.md

Think in English, reply in Chinese.

## Project

A Steam Frame plugin that adds a switch at the end of Quick Access Menu → Quick Settings. The switch turns haptic feedback on or off for the controller's virtual cursor as it touches a UI button.

## Workflow

- Implement tickets directly on the main branch. Do not create a git branch or worktree per ticket.

## Checks

- All checks: `bash scripts/check.sh`
- Full unit suite: `python -m unittest discover -s tests`
- One test file: `python -m unittest tests.test_daemon -v`
- Ruff lint + format (whole repo, via uv): `uvx ruff@0.17.1 check --fix . && uvx ruff@0.17.1 format --check .`
- Shellcheck: `shellcheck install.sh uninstall.sh installation_lib.sh scripts/check.sh scripts/probe-device.sh`
- Shell syntax: `bash -n install.sh && bash -n uninstall.sh && bash -n installation_lib.sh`

## Device

Read-only endpoint probe: `scripts/probe-device.sh`. Device facts:
`docs/research/t0-device-probe.md`. SSH alias setup: `scripts/setup-sf-ssh.sh`.

## Agent skills

### Issue tracker

Issues and specs live as GitHub issues (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles, label strings equal to their names. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
