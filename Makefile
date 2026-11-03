.PHONY: help sync-upstream-check sync-upstream lint

help:
	@echo "make sync-upstream-check  # show upstream changes since our pins (read-only)"
	@echo "make sync-upstream TASK=ID  # merge latest upstream develop into apps/ (keeps our edits)"
	@echo "make lint                 # run all repo checks (pre-commit)"

sync-upstream-check:
	scripts/sync-upstream.sh all --dry-run

sync-upstream:
	@test -n "$(TASK)" || { echo 'Usage: make sync-upstream TASK=P2-T4'; exit 1; }
	scripts/sync-upstream.sh all --task $(TASK)

lint:
	pre-commit run --all-files
