#!/usr/bin/env bash
set -uo pipefail

echo "=== Post-provision hook (ServiceNow MCP) ==="

# Load azd environment variables as exports
echo "Loading azd environment variables..."
while IFS= read -r line; do
    export "$line"
done < <(azd env get-values 2>/dev/null | sed 's/^//' | tr -d '"')

# Install hook dependencies (stdlib-only for now, but keeps pattern consistent)
if [ -f hooks/requirements.txt ]; then
    echo "Installing hook dependencies..."
    pip install --quiet -r hooks/requirements.txt
fi

# Run the post-provision script (non-fatal)
echo "Running post-provision script..."
python hooks/postprovision.py || echo "Post-provision script completed with warnings (see above)"
