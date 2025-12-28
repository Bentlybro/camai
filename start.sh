#!/bin/bash
# CAMAI - Quick Start Script for Jetson
# Usage: ./start.sh

set -e
cd "$(dirname "$0")"

IMAGE="ultralytics/ultralytics:latest-jetson-jetpack6"

# Check if engine exists, if not we need to build first
if [ ! -f "yolo11n.engine" ]; then
    echo "First run - exporting YOLO11n to TensorRT..."
    echo "This takes ~7 minutes, but only happens once."

    sudo docker run --rm --ipc=host --runtime=nvidia \
        --net=host --privileged \
        -v "$(pwd)":/app -w /app \
        $IMAGE \
        yolo export model=yolo11n.pt format=engine half=True

    echo "Export complete!"
fi

# Install python-dotenv if needed and run
echo "Starting CAMAI..."
sudo docker run -it --rm --ipc=host --runtime=nvidia \
    --net=host --privileged \
    -v "$(pwd)":/app -w /app \
    $IMAGE \
    bash -c "pip install -q python-dotenv && python3 run.py"
