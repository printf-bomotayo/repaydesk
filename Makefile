# Thin wrapper: every task lives in scripts/dev.py so Windows needs no make.
PY ?= python
.PHONY: env up up-langfuse seed ingest match directory score down reset test itest lint urls web webtest gateway
env up up-langfuse seed ingest match directory score down reset test itest lint urls web webtest gateway:
	$(PY) scripts/dev.py $@
