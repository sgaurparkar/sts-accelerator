#!/bin/bash
# One-shot setup for sts-accelerator in a fresh Cloud Shell session.
# Usage:
#   cd sts-accelerator
#   bash setup.sh

set -e

echo "==> Creating/activating virtual environment"
if [ ! -d "venv" ]; then
  python3 -m venv venv
fi

source venv/bin/activate

echo "==> Installing Python dependencies"
pip install --upgrade pip -q
pip install -r requirements.txt -q

echo "==> Installing SQL Server ODBC driver (msodbcsql18)"

# Remove old repo entries if present
sudo rm -f /etc/apt/sources.list.d/mssql-release.list
sudo rm -f /etc/apt/sources.list.d/microsoft-prod.list

# Add Microsoft GPG key
curl -fsSL https://packages.microsoft.com/keys/microsoft.asc \
  | gpg --dearmor > microsoft.gpg

sudo install -o root -g root -m 644 microsoft.gpg \
  /usr/share/keyrings/microsoft-prod.gpg

rm -f microsoft.gpg

# Add Microsoft Debian 12 repo
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/microsoft-prod.gpg] https://packages.microsoft.com/debian/12/prod bookworm main" \
  | sudo tee /etc/apt/sources.list.d/microsoft-prod.list > /dev/null

echo "==> Updating package lists"
sudo apt-get update

echo "==> Installing ODBC packages"
sudo apt-get install -y unixodbc unixodbc-dev

echo "==> Installing SQL Server driver"
ACCEPT_EULA=Y sudo apt-get install -y msodbcsql18

echo "==> Verifying installation"
ldconfig -p | grep libodbc || true

echo "==> Setup complete"
echo "==> Virtual environment is active"
echo "==> Run: python3 main.py"
