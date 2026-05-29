# Check if uv is installed
if (!(Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv..."
    try {
        Invoke-Expression "& { $(irm https://astral.sh/uv/install.ps1) }"
        Write-Host "uv installed successfully."
    } catch {
        Write-Host "Failed to install uv."
        exit 1
    }
} else {
    Write-Host "uv is already installed."
}

# Check if ollama is installed
if (!(Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Ollama via winget..."
    try {
        winget install Ollama.Ollama
        Write-Host "Ollama installed successfully."
    } catch {
        Write-Host "Failed to install Ollama via winget. Please download from https://ollama.com/download/windows"
        exit 1
    }
} else {
    Write-Host "Ollama is already installed."
}

# Install dependencies
Write-Host "Installing dependencies..."
try {
    uv sync
    Write-Host "Dependencies installed successfully."
} catch {
    Write-Host "Failed to install dependencies."
    exit 1
}

# Pull Ollama model
Write-Host "Pulling Ollama model qwen3:8b..."
try {
    ollama pull qwen3:8b
    Write-Host "Model pulled successfully."
} catch {
    Write-Host "Failed to pull model."
    exit 1
}

# Create directories
New-Item -ItemType Directory -Force -Path data
New-Item -ItemType Directory -Force -Path logs
Write-Host "Created data/ and logs/ directories."

# Copy .env.example to .env if not exists
if (!(Test-Path .env)) {
    Copy-Item .env.example .env
    Write-Host "Copied .env.example to .env"
} else {
    Write-Host ".env already exists."
}

Write-Host "Installation complete."