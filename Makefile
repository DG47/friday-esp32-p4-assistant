.PHONY: dev dev-local tls-local test test-local smoke firmware-build firmware-flash firmware-monitor

dev:
	docker compose up --build

dev-local:
	@test -f data/tls/friday-backend.crt -a -f data/tls/friday-backend.key || \
	  (echo "Run: make tls-local LAN_IP=<this Mac's LAN IP>" >&2; exit 1)
	PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --ws-ping-timeout 60 \
	  --ssl-certfile data/tls/friday-backend.crt --ssl-keyfile data/tls/friday-backend.key

tls-local:
	./scripts/setup_local_tls.sh $${LAN_IP:-}

test:
	docker compose run --rm backend python -m unittest discover -s tests -v

test-local:
	PYTHONPATH=backend .venv/bin/python -m unittest discover -s backend/tests -v

smoke:
	ASSISTANT_CA_CERT=data/tls/friday-ca.crt ASSISTANT_URL=https://localhost:8000 ./scripts/smoke_test.sh

firmware-build:
	./scripts/idf.sh build

firmware-flash:
	./scripts/idf.sh -p $${PORT:?Set PORT=/dev/cu.usbmodem...} flash

firmware-monitor:
	./scripts/idf.sh -p $${PORT:?Set PORT=/dev/cu.usbmodem...} monitor
