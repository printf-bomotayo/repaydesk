# Thin wrapper: every task lives in scripts/dev.py so Windows needs no make.
PY ?= python
.PHONY: up up-langfuse seed down reset test itest lint urls
up up-langfuse seed down reset test itest lint urls:
	$(PY) scripts/dev.py $@
