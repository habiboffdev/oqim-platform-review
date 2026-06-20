# Start all OQIM Business services
# Usage: overmind start   (after: brew install overmind)
#        honcho start     (after: pip install honcho)

docker: docker compose up
api:    bash -c 'cd backend && source venv/bin/activate && python main.py'
webz:   bash -c 'cd telegram-web-z && npm run dev -- --port 1235'
web:    bash -c 'cd frontend && npm run dev'
