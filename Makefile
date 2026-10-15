.PHONY: test init tools serve cases
PYTHON ?= python3

test:
	$(PYTHON) -m unittest discover -s tests -v

init:
	$(PYTHON) -m agentlab init

tools:
	$(PYTHON) -m agentlab tools

serve:
	$(PYTHON) -m agentlab serve

cases:
	$(PYTHON) -m agentlab bench list
