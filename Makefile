# Thin wrapper: every task lives in scripts/dev.py so Windows needs no make.
PY ?= python
.PHONY: env up up-langfuse seed ingest match directory score investigate draft eval demo down reset test itest lint urls web webtest gateway mcp
env up up-langfuse seed ingest match directory score investigate draft eval demo down reset test itest lint urls web webtest gateway mcp:
	$(PY) scripts/dev.py $@
