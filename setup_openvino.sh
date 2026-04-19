#!/bin/bash
# OpenVINO Quick Setup Script for Linux/Mac
# This script sets up and tests the OpenVINO real-time inference

echo ""
echo "========================================"
echo "  OpenVINO Real-Time Setup"
echo "========================================"
echo ""

# Check Python installation
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python 3 is not installed or not in PATH"
    echo "Please install Python 3.8+ from https://www.python.org"
    exit 1
fi

echo "[1] Checking Python version..."
python3 --version
echo ""

# Check if in correct directory
if [ ! -f "models/requirements.txt" ]; then
    echo "[ERROR] models/requirements.txt not found"
    echo "Please ensure you're in the project root directory"
    exit 1
fi

echo "[2] Installing dependencies..."
echo "(This may take a few minutes)"
echo ""
python3 -m pip install -q -r models/requirements.txt
if [ $? -ne 0 ]; then
    echo "[ERROR] Failed to install dependencies"
    exit 1
fi
echo ""
echo "[OK] Dependencies installed successfully"
echo ""

# Check model files
echo "[3] Checking model files..."
if [ ! -f "best_openvino_model/best.xml" ]; then
    echo "[ERROR] best_openvino_model/best.xml not found"
    exit 1
fi
if [ ! -f "best_openvino_model/best.bin" ]; then
    echo "[ERROR] best_openvino_model/best.bin not found"
    exit 1
fi
echo "[OK] Model files found"
echo ""

# Run setup verification script
echo "[4] Running verification tests..."
python3 setup_openvino.py
if [ $? -ne 0 ]; then
    echo "[ERROR] Verification failed"
    exit 1
fi

echo ""
echo "========================================"
echo ""
echo "  SETUP COMPLETE!"
echo ""
echo "Next steps:"
echo "1. Start the API server:"
echo "   python3 main.py"
echo ""
echo "2. Or test real-time detection:"
echo "   python3 realtime_stream.py"
echo ""
echo "3. For more examples:"
echo "   See OPENVINO_EXAMPLES.md"
echo ""
echo "========================================"
echo ""
