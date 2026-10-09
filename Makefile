# Thin wrapper: every task lives in scripts/dev.py so Windows needs no make.
PY ?= python
.PHONY: up up-langfuse seed ingest match score down reset test itest lint urls web webtest
up up-langfuse seed ingest match score down reset test itest lint urls web webtest:
	$(PY) scripts/dev.py $@
