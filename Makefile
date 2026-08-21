# Thin delegator. The real task definitions live in [tool.poe.tasks] in
# pyproject.toml, so they run identically with or without GNU make — `make` is
# not installed on the primary (Windows) dev machine.
#
#   make test-unit        ==        uv run poe test-unit
#
POE := uv run poe

.PHONY: help up down logs ps smoke test test-unit test-integration test-e2e lint fmt typecheck lock contracts

help:
	@$(POE) --help

up:
	@$(POE) up

down:
	@$(POE) down

logs:
	@$(POE) logs

ps:
	@$(POE) ps

smoke:
	@$(POE) smoke

test-unit:
	@$(POE) test-unit

test-integration:
	@$(POE) test-integration

test-e2e:
	@$(POE) test-e2e

test:
	@$(POE) test

lint:
	@$(POE) lint

fmt:
	@$(POE) fmt

typecheck:
	@$(POE) typecheck

lock:
	@$(POE) lock

contracts:
	@$(POE) contracts
