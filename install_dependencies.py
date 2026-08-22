import os
import sys
import subprocess

def run_sys_pip():
    print("[Drishya 2.0 Setup] Setting up Python dependencies with SSL fallbacks...")
    
    # Pip configuration to trust standard Python package repositories
    cmd = [
        sys.executable, "-m", "pip", "install", 
        "--trusted-host", "pypi.org", 
        "--trusted-host", "files.pythonhosted.org", 
        "--trusted-host", "huggingface.co",
        "-r", "backend/requirements.txt"
    ]
    
    print(f"Executing: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, check=True)
        print("[Drishya 2.0 Setup] Dependencies installed successfully!")
    except subprocess.CalledProcessError as e:
        print(f"[Drishya 2.0 Setup] Installation failed with exit code: {e.returncode}")
        print("Tip: If you continue to see SSL validation failures, consider running:")
        print("  pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host huggingface.co <package>")

if __name__ == "__main__":
    run_sys_pip()
