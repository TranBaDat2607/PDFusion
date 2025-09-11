#!/usr/bin/env bash
# Docker entrypoint script for Desktop PDF Translator

# Set environment variables
export DOCKER_ENV=true
export DISPLAY=:0

# Start Xvfb (virtual framebuffer) for headless GUI support
Xvfb :0 -screen 0 1024x768x24 &
XVFB_PID=$!

# Wait a moment for Xvfb to start
sleep 2

# Create basic fluxbox configuration to avoid warnings
mkdir -p /home/appuser/.fluxbox
cat > /home/appuser/.fluxbox/init << EOF
session.styleFile: /usr/share/fluxbox/styles/default
session.screen0.workspacewarping: false
session.screen0.toolbar.visible: true
EOF

# Start a minimal window manager
fluxbox &
FLUXBOX_PID=$!

# Wait for window manager to start
sleep 2

# Run the Python application
python /app/main.py

# Capture exit code
EXIT_CODE=$?

# Clean up background processes
kill $XVFB_PID $FLUXBOX_PID 2>/dev/null || true

# Exit with the same code as the Python application
exit $EXIT_CODE