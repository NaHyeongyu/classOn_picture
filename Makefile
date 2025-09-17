SHELL := /bin/bash

INPUT ?= data/input
OUT ?= data/output
TOPK ?= 3
MCS ?= 5
PY ?= python

.PHONY: setup run preview clean web

setup:
	bash scripts/setup_venv.sh

run:
	. .venv/bin/activate && $(PY) scripts/run_pipeline.py --input $(INPUT) --out $(OUT) --topk $(TOPK) --min-cluster-size $(MCS)

preview:
	. .venv/bin/activate && $(PY) scripts/preview_clusters.py --out $(OUT)

clean:
	rm -rf $(OUT)/faces $(OUT)/cache $(OUT)/clusters.json $(OUT)/report.html

web:
	. .venv/bin/activate && $(PY) scripts/web_ui.py
