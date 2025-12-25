#!/usr/bin/env python3
"""
Main entry point for Desktop PDF Translator.

This is the main file you run during development: python main.py

For Vietnamese language priority PDF translation with Windows desktop GUI.
"""

import sys
import os
import logging
from pathlib import Path

# Add src directory to path for development
src_path = Path(__file__).parent / "src"
if src_path.exists():
    sys.path.insert(0, str(src_path))

from desktop_pdf_translator.gui.main_window import main as gui_main
from desktop_pdf_translator.config import get_config_manager
from desktop_pdf_translator.translators import TranslatorFactory


def setup_logging(debug_mode: bool = False):
    """Setup application logging."""
    level = logging.DEBUG if debug_mode else logging.INFO
    
    # Create logs directory
    log_dir = Path.home() / "AppData" / "Local" / "PDFusion" / "logs"
    
    log_dir.mkdir(parents=True, exist_ok=True)
    
    # Setup logging configuration with UTF-8 encoding for Vietnamese text
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_dir / "app.log", encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

    # Force UTF-8 encoding for console output on Windows
    if sys.platform == 'win32':
        try:
            import codecs
            sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
            sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')
        except Exception:
            pass  # Fallback to default if UTF-8 reconfiguration fails
    
    # Reduce noise from some libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("chromadb.telemetry").setLevel(logging.CRITICAL)  # Silence telemetry errors


def check_dependencies():
    """Check if all required dependencies are available."""
    missing_deps = []
    
    # Check PySide6
    try:
        import PySide6
    except ImportError:
        missing_deps.append("PySide6")
    
    # Check BabelDOC (optional but recommended)
    try:
        import babeldoc
        print(f"✓ BabelDOC available (version: {babeldoc.__version__})")
    except ImportError:
        print("⚠️  BabelDOC not available - some features will be limited")
        print("   Install with: pip install babeldoc")
    
    # Check translation services
    available_services = []
    
    try:
        import openai
        available_services.append("OpenAI")
    except ImportError:
        print("⚠️  OpenAI library not available")
        print("   Install with: pip install openai")
    
    try:
        import google.generativeai
        available_services.append("Gemini")
    except ImportError:
        print("⚠️  Google AI library not available") 
        print("   Install with: pip install google-generativeai")
    
    if missing_deps:
        print(f"❌ Missing required dependencies: {', '.join(missing_deps)}")
        print("Install with: pip install -r requirements.txt")
        return False
    
    if not available_services:
        print("❌ No translation services available")
        print("Install at least one: pip install openai google-generativeai")
        return False
    
    print(f"✓ Available translation services: {', '.join(available_services)}")
    return True


def check_configuration():
    """Check application configuration."""
    try:
        config_manager = get_config_manager()
        settings = config_manager.settings
        
        print(f"✓ Configuration loaded from: {config_manager.get_default_config_path()}")
        
        # Check translation service configuration
        available_services = TranslatorFactory.get_available_services()
        
        if not available_services:
            print("⚠️  No translation services configured")
            print("   Set API keys via environment variables:")
            print("   - OPENAI_API_KEY for OpenAI")  
            print("   - GEMINI_API_KEY for Google Gemini")
            return False
        
        # Check preferred service
        preferred_service = settings.translation.preferred_service
        is_valid, message = TranslatorFactory.validate_service_availability(preferred_service)
        
        if is_valid:
            print(f"✓ Preferred translation service ({preferred_service}) is ready")
        else:
            print(f"⚠️  Preferred service ({preferred_service}) issue: {message}")
            
            # Check if any service is available
            for service in available_services:
                is_valid, _ = TranslatorFactory.validate_service_availability(service)
                if is_valid:
                    print(f"✓ Alternative service available: {service}")
                    break
            else:
                print("❌ No translation services are properly configured")
                return False
        
        return True
        
    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return False


def print_startup_info():
    """Print application startup information."""
    print("=" * 60)
    print("🇻🇳 DESKTOP PDF TRANSLATOR - Vietnamese Priority")
    print("=" * 60)
    print()
    print("Features:")
    print("• Multi-panel GUI (Original PDF | Controls | Translated PDF)")
    print("• Vietnamese language optimization")
    print("• OpenAI & Google Gemini support")
    print("• BabelDOC integration for layout preservation")
    print("• Future RAG chat integration")
    print()
    print("Quick Start:")
    print("1. Set API keys: OPENAI_API_KEY or GEMINI_API_KEY")
    print("2. Click 'Browse' to select a PDF file")
    print("3. Choose translation settings") 
    print("4. Click 'Translate' to start")
    print()
    
    # Environment info
    print(f"Python: {sys.version.split()[0]}")
    print(f"Platform: {sys.platform}")


def main():
    """Main application entry point."""
    print_startup_info()
    
    # Check dependencies
    print("Checking dependencies...")
    if not check_dependencies():
        input("Press Enter to exit...")
        return 1
    
    print()
    
    # Check configuration
    print("Checking configuration...")
    config_ok = check_configuration()
    
    print()
    
    if not config_ok:
        print("⚠️  Configuration issues detected, but app will still start")
        print("   You can configure services in the Settings menu")
    
    print("Starting application...")
    print()
    
    # Setup logging
    try:
        config_manager = get_config_manager()
        debug_mode = config_manager.settings.debug_mode
    except:
        debug_mode = False
    
    setup_logging(debug_mode)
    
    # Start GUI
    try:
        return gui_main()
    except KeyboardInterrupt:
        print("\n👋 Application interrupted by user")
        return 0
    except Exception as e:
        logging.exception("Fatal error starting application")
        print(f"❌ Fatal error: {e}")
        input("Press Enter to exit...")
        return 1


if __name__ == "__main__":
    sys.exit(main())