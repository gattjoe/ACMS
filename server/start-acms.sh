#!/bin/bash

# Check if .acms virtual environment exists in current directory
if [ ! -d ".acms" ]; then
    echo "Virtual environment .acms not found. Creating..."
    python3 -m venv .acms
    if [ $? -eq 0 ]; then
        echo "Virtual environment .acms created successfully."
    else
        echo "Failed to create virtual environment .acms" >&2
        exit 1
    fi
fi

# Activate the virtual environment
echo "Activating virtual environment .acms..."
source .acms/bin/activate

if [ $? -eq 0 ]; then
    echo "Virtual environment .acms activated successfully."
    echo "Python path: $(which python)"
    echo "Python version: $(python --version)"
else
    echo "Failed to activate virtual environment .acms" >&2
    exit 1
fi

# Launch acms.py as a background process with output redirected to acms.log
echo "Starting ACMS server in background..."
python acms.py > acms.log 2>&1 &
ACMS_PID=$!

echo "ACMS server started with PID: $ACMS_PID"
echo "Output is being logged to: acms.log"
echo "To stop the server, use: kill $ACMS_PID"