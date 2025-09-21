#!/usr/bin/env python3
"""
PDFusion Dependencies Installer
Installs dependencies in stages to avoid resolution-too-deep errors
"""

import subprocess
import sys
import time
from pathlib import Path

def run_command(cmd, description):
    """Run a command and handle errors"""
    print(f"\n{'='*60}")
    print(f"🔧 {description}")
    print(f"{'='*60}")
    print(f"Running: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
        print("✅ Success!")
        if result.stdout:
            print(f"Output: {result.stdout[-500:]}")  # Last 500 chars
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Error: {e}")
        if e.stdout:
            print(f"Stdout: {e.stdout[-500:]}")
        if e.stderr:
            print(f"Stderr: {e.stderr[-500:]}")
        return False

def install_stage(requirements_file, stage_name):
    """Install a specific requirements file"""
    if not Path(requirements_file).exists():
        print(f"⚠️  {requirements_file} not found, skipping {stage_name}")
        return True
    
    return run_command([
        sys.executable, "-m", "pip", "install", "-r", requirements_file
    ], f"Installing {stage_name}")

def main():
    """Main installation process"""
    print("🚀 PDFusion Dependencies Installer")
    print("This will install dependencies in stages to avoid conflicts")
    
    # Check if we're in the right directory
    if not Path("requirements_core.txt").exists():
        print("❌ Please run this script from the PDFusion root directory")
        sys.exit(1)
    
    # Upgrade pip first
    if not run_command([
        sys.executable, "-m", "pip", "install", "--upgrade", "pip"
    ], "Upgrading pip"):
        print("❌ Failed to upgrade pip")
        sys.exit(1)
    
    # Installation stages
    stages = [
        ("requirements_core.txt", "Core Dependencies (GUI, PDF, Translation)"),
        ("requirements_scientific.txt", "Scientific Computing"),
        ("requirements_ai.txt", "AI/ML Libraries"),
        ("requirements_rag.txt", "RAG and Web Research")
    ]
    
    failed_stages = []
    
    for req_file, stage_name in stages:
        print(f"\n⏳ Starting {stage_name}...")
        time.sleep(1)  # Brief pause
        
        if install_stage(req_file, stage_name):
            print(f"✅ {stage_name} completed successfully!")
        else:
            print(f"❌ {stage_name} failed!")
            failed_stages.append(stage_name)
            
            # Ask user if they want to continue
            response = input(f"\nContinue with remaining stages? (y/n): ").lower()
            if response != 'y':
                break
    
    # Summary
    print(f"\n{'='*60}")
    print("📋 INSTALLATION SUMMARY")
    print(f"{'='*60}")
    
    if not failed_stages:
        print("🎉 All dependencies installed successfully!")
        print("\n🚀 You can now run: python main.py")
    else:
        print(f"⚠️  Some stages failed: {', '.join(failed_stages)}")
        print("\n💡 You can:")
        print("1. Try installing failed stages manually")
        print("2. Run the app with core dependencies only")
        print("3. Check the error messages above")
    
    # Show installed packages
    print(f"\n📦 Checking installed packages...")
    run_command([sys.executable, "-m", "pip", "list"], "Installed packages")

if __name__ == "__main__":
    main()
