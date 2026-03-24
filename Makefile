.PHONY: serve dev test lint clean

PID_FILE := /tmp/morpheus-mcp.pid

serve:
	morpheus-mcp

dev:
	@echo "Starting morpheus-mcp with auto-restart on src/ changes..."
	@if command -v inotifywait >/dev/null 2>&1; then \
		$(MAKE) _dev-inotify; \
	else \
		echo "(install inotify-tools for instant reload; falling back to polling)"; \
		$(MAKE) _dev-poll; \
	fi

_dev-inotify:
	@while true; do \
		morpheus-mcp & echo $$! > $(PID_FILE); \
		echo "[morpheus-mcp] started (pid $$(cat $(PID_FILE)))"; \
		inotifywait -r -e modify,create,delete src/; \
		echo "[morpheus-mcp] source changed — restarting..."; \
		kill $$(cat $(PID_FILE)) 2>/dev/null; wait $$(cat $(PID_FILE)) 2>/dev/null; \
	done

_dev-poll:
	@touch /tmp/.morpheus-mcp-lastcheck; \
	while true; do \
		morpheus-mcp & echo $$! > $(PID_FILE); \
		echo "[morpheus-mcp] started (pid $$(cat $(PID_FILE)))"; \
		while true; do \
			sleep 2; \
			if [ -n "$$(find src/ -name '*.py' -newer /tmp/.morpheus-mcp-lastcheck 2>/dev/null)" ]; then \
				touch /tmp/.morpheus-mcp-lastcheck; \
				echo "[morpheus-mcp] source changed — restarting..."; \
				kill $$(cat $(PID_FILE)) 2>/dev/null; wait $$(cat $(PID_FILE)) 2>/dev/null; \
				break; \
			fi; \
		done; \
	done

test:
	python3 -m pytest tests/ -v

lint:
	python3 -m ruff check src/ tests/
	python3 -m mypy src/

clean:
	rm -f $(PID_FILE) /tmp/.morpheus-mcp-lastcheck
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
