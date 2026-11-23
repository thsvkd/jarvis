# 개발용 진입점. 로직은 모두 scripts/*.py 에 있고 여기서는 부르기만 한다(Windows 는 README '개발' 절대로 winget 으로 make 설치).

ifeq ($(OS),Windows_NT)
PYTHON ?= python
else
PYTHON ?= python3
endif

.PHONY: help deps test lint run
.DEFAULT_GOAL := help

help: ## 이 목록을 보여 준다(인자 없이 make 를 불러도 같다)
	@$(PYTHON) scripts/help.py

deps: ## .venv 에 Python 3.13 과 psutil 을 받는다(uv 필요)
	$(PYTHON) scripts/deps.py

test: ## 단위 테스트 + 화면 공용 JS. 일부만: make test ARGS="tests.test_core -v"
	$(PYTHON) scripts/test.py $(ARGS)

lint: ## VS Code 저장 시와 같게 포매팅. make lint FILES="a.py b.mjs", 확인만: CHECK=1
	$(PYTHON) scripts/lint.py $(if $(CHECK),--check) $(FILES)

run: ## 게이트웨이를 http://127.0.0.1:9390/ 로 띄운다. core: make run CONFIG=path/to/config.toml, 포트: PORT=9391
	$(PYTHON) scripts/run.py $(if $(PORT),--port $(PORT)) $(if $(CONFIG),--config $(CONFIG))
