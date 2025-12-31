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

# Install dependencies and run
echo "Starting CAMAI..."
sudo docker run -it --rm --ipc=host --runtime=nvidia \
    --net=host --privileged \
    -v "$(pwd)":/app -w /app \
    $IMAGE \
    bash -c "apt-get update -qq && apt-get install -y -qq ffmpeg > /dev/null && \
             pip install -q python-dotenv onvif-zeep fastapi 'uvicorn[standard]' firebase-admin 'passlib[bcrypt]' 'python-jose[cryptography]' && \
             WSDL_DIR=\$(python3 -c 'import onvif; import os; print(os.path.dirname(onvif.__file__))') && \
             if [ ! -f \"\$WSDL_DIR/wsdl/devicemgmt.wsdl\" ]; then \
               echo 'Downloading ONVIF WSDL files...' && \
               git clone --depth 1 https://github.com/quatanium/python-onvif.git /tmp/onvif-wsdl && \
               cp -r /tmp/onvif-wsdl/wsdl \"\$WSDL_DIR/\" && \
               rm -rf /tmp/onvif-wsdl; \
             fi && \
             python3 run.py"
