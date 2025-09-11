#!/usr/bin/env python3
"""
Docker entrypoint for Desktop PDF Translator.
"""

import os
import sys
import subprocess
import signal
import time

# Set the DOCKER_ENV variable to indicate we're in Docker
os.environ['DOCKER_ENV'] = 'true'

def signal_handler(sig, frame):
    """Handle shutdown signals gracefully."""
    print("\nShutting down gracefully...")
    sys.exit(0)

def main():
    """Main entrypoint function."""
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Check if we're in Docker
    if os.environ.get('DOCKER_ENV', '').lower() == 'true':
        print("Running in Docker environment")
    
    # Import and run the main application
    try:
        # Add src to path
        sys.path.insert(0, '/app/src')
        
        # Import the main GUI function
        from desktop_pdf_translator.gui.main_window import main as gui_main
        
        # Run the GUI application
        return gui_main()
    except ImportError as e:
        print(f"Failed to import main application: {e}")
        # Fallback to command-line mode
        try:
            from main import main as app_main
            return app_main()
        except Exception as e2:
            print(f"Failed to run application: {e2}")
            return 1
    except Exception as e:
        print(f"Error running application: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())