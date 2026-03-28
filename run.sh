#!/bin/bash

# Script to start the AI multi-agent system

echo "Starting AI Multi-Agent System..."

# Check if virtual environment exists, if not create and activate it
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo "Virtual environment created."
fi

source .venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt

# Start the backend (Flask API)
echo "Starting backend API..."
cd backend
python main.py &

# Start the frontend (simple HTTP server for static files)
echo "Starting frontend..."
cd ../frontend
python -m http.server 8080 &

echo "System started. Backend running on port 5001, Frontend on port 8080."
echo "Press CTRL+C to stop the system."

# Wait for any process to exit
wait -n

# Exit with status of process that exited first
exit $?