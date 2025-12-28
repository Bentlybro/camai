#!/bin/bash
# CAMAI Setup Script
# Pulls Docker image and exports model

set -e
cd "$(dirname "$0")"

IMAGE="ultralytics/ultralytics:latest-jetson-jetpack6"

echo "========================================"
echo "CAMAI - Jetson AI Camera Setup"
echo "========================================"

# Pull Docker image
echo "[1/3] Pulling Docker image..."
sudo docker pull $IMAGE

# Setup .env if needed
echo "[2/3] Setting up configuration..."
if [ ! -f .env ]; then
    cp config/.env.example .env
    echo "Created .env - please edit with your RTSP URL!"
    echo "  nano .env"
fi

# Export model
echo "[3/3] Exporting YOLO11n to TensorRT..."
if [ ! -f "yolo11n.engine" ]; then
    sudo docker run --rm --ipc=host --runtime=nvidia \
        --net=host --privileged \
        -v "$(pwd)":/app -w /app \
        $IMAGE \
        yolo export model=yolo11n.pt format=engine
    echo "Model exported!"
else
    echo "Model already exists, skipping."
fi

mkdir -p logs snapshots

echo ""
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo ""
echo "Next:"
echo "  1. Edit .env with your RTSP URL"
echo "  2. Run: ./start.sh"
echo "  3. View: http://$(hostname -I | awk '{print $1}'):8080/stream"
echo "========================================"
