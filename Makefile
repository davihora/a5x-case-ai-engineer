.PHONY: setup test run eval lock clean

VENV := .venv
# python a usar: o do venv local se ele existir, senao o python3 do sistema.
# resolvido em tempo de execucao do alvo (funciona mesmo em `make setup test`).
PYBIN = $$([ -x $(VENV)/bin/python ] && echo $(VENV)/bin/python || echo python3)

setup: ## bootstrap completo: cria o venv e instala as deps pinadas (uv se houver; senao venv + pip)
	@if command -v uv >/dev/null 2>&1; then \
		echo ">> uv detectado -> uv sync --frozen (usa uv.lock)"; \
		uv sync --frozen; \
	else \
		echo ">> uv ausente -> python3 -m venv + pip install -r requirements.txt"; \
		python3 -m venv $(VENV); \
		$(VENV)/bin/python -m pip install --quiet --upgrade pip; \
		$(VENV)/bin/pip install -r requirements.txt; \
	fi
	@echo ">> setup OK. Proximos: make test && make run (nao precisa ativar o venv)"

test: ## roda os specs (pytest -q)
	@PY="$(PYBIN)"; echo ">> usando $$PY"; "$$PY" -m pytest -q

run: ## pipeline ponta-a-ponta -> gera dashboard.html
	@PY="$(PYBIN)"; "$$PY" run.py

eval: ## scorecard offline (golden + adversarial). A5X_USE_LLM=1 + ANTHROPIC_API_KEY ativa o juiz LLM
	@PY="$(PYBIN)"; "$$PY" -m agent.evals

lock: ## (manutencao, requer uv) re-resolve deps e re-exporta requirements.txt
	uv lock
	uv export --format requirements-txt --no-hashes --no-emit-project -o requirements.txt

clean: ## remove venv, caches e saidas geradas pelo pipeline
	rm -rf $(VENV) .pytest_cache __pycache__ agent/__pycache__ dashboard/__pycache__ tests/__pycache__ \
		out_action.parquet out_decision_trace.parquet dashboard.html
