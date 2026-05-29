#!/bin/bash

# Install uv if not present
if ! command -v uv &> /dev/null; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    echo "uv installed successfully."
else
    echo "uv is already installed."
fi

# Install ollama
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
    echo "Ollama installed successfully."
else
    echo "Ollama is already installed."
fi

# Install dependencies
echo "Installing dependencies..."
uv sync
echo "Dependencies installed successfully."

# Pull Ollama model
echo "Pulling Ollama model qwen3:8b..."
ollama pull qwen3:8b
echo "Model pulled successfully."

# Create directories
mkdir -p data logs
echo "Created data/ and logs/ directories."

# Copy .env.example to .env if not exists
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Copied .env.example to .env"
else
    echo ".env already exists."
fi

echo "Installation complete."