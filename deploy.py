#!/usr/bin/env python3
"""
Deployment helper script for Form Translator Backend
This script helps set up environment variables and deploy to Fly.io
"""

import os
import subprocess
import sys
from pathlib import Path

def check_flyctl():
    """Check if flyctl is installed"""
    try:
        result = subprocess.run(['flyctl', 'version'], capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ flyctl is installed")
            return True
    except FileNotFoundError:
        pass
    
    print("❌ flyctl is not installed")
    print("📥 Install it with: brew install flyctl")
    print("🔗 Or visit: https://fly.io/docs/hands-on/install-flyctl/")
    return False

def check_env_file():
    """Check if .env file exists with required variables"""
    env_file = Path('.env')
    if not env_file.exists():
        print("❌ .env file not found")
        print("📋 Copy env.example to .env and fill in your values")
        return False
    
    required_vars = ['OPENAI_API_KEY', 'SECRET_KEY']
    missing_vars = []
    
    with open('.env', 'r') as f:
        content = f.read()
        for var in required_vars:
            if f"{var}=" not in content or f"{var}=your-" in content:
                missing_vars.append(var)
    
    if missing_vars:
        print(f"❌ Missing or incomplete environment variables: {', '.join(missing_vars)}")
        print("📝 Please update your .env file with real values")
        return False
    
    print("✅ .env file looks good")
    return True

def check_google_credentials():
    """Check if Google service account file exists"""
    cred_file = Path('Form Translator DB IAM.json')
    if not cred_file.exists():
        print("❌ Google service account file not found")
        print("📁 Make sure 'Form Translator DB IAM.json' is in the project root")
        return False
    
    print("✅ Google credentials file found")
    return True

def deploy_to_fly():
    """Deploy the application to Fly.io"""
    print("\n🚀 Starting deployment to Fly.io...")
    
    # Check if app exists
    try:
        result = subprocess.run(['flyctl', 'status'], capture_output=True, text=True)
        if result.returncode != 0:
            print("📱 Creating new Fly.io app...")
            subprocess.run(['flyctl', 'launch', '--no-deploy'], check=True)
    except subprocess.CalledProcessError:
        print("❌ Failed to check/create Fly.io app")
        return False
    
    # Set secrets
    print("🔐 Setting up secrets...")
    env_file = Path('.env')
    if env_file.exists():
        with open('.env', 'r') as f:
            for line in f:
                if '=' in line and not line.startswith('#'):
                    key, value = line.strip().split('=', 1)
                    if key in ['OPENAI_API_KEY', 'SECRET_KEY'] and not value.startswith('your-'):
                        try:
                            subprocess.run(['flyctl', 'secrets', 'set', f'{key}={value}'], check=True)
                            print(f"✅ Set secret: {key}")
                        except subprocess.CalledProcessError:
                            print(f"❌ Failed to set secret: {key}")
    
    # Set Google credentials as secret
    cred_file = Path('Form Translator DB IAM.json')
    if cred_file.exists():
        try:
            with open(cred_file, 'r') as f:
                creds_content = f.read()
            subprocess.run(['flyctl', 'secrets', 'set', f'GOOGLE_APPLICATION_CREDENTIALS_JSON={creds_content}'], check=True)
            print("✅ Set Google credentials secret")
        except subprocess.CalledProcessError:
            print("❌ Failed to set Google credentials secret")
    
    # Deploy
    print("🚀 Deploying to Fly.io...")
    try:
        subprocess.run(['flyctl', 'deploy'], check=True)
        print("✅ Deployment successful!")
        
        # Get app info
        result = subprocess.run(['flyctl', 'info'], capture_output=True, text=True)
        if result.returncode == 0:
            print("\n📋 App Information:")
            print(result.stdout)
        
        return True
    except subprocess.CalledProcessError:
        print("❌ Deployment failed")
        return False

def main():
    """Main deployment function"""
    print("🔧 Form Translator Backend - Deployment Helper")
    print("=" * 50)
    
    # Pre-flight checks
    checks_passed = True
    
    if not check_flyctl():
        checks_passed = False
    
    if not check_env_file():
        checks_passed = False
    
    if not check_google_credentials():
        checks_passed = False
    
    if not checks_passed:
        print("\n❌ Pre-flight checks failed. Please fix the issues above.")
        sys.exit(1)
    
    print("\n✅ All pre-flight checks passed!")
    
    # Ask user if they want to proceed
    response = input("\n🚀 Ready to deploy to Fly.io? (y/N): ").strip().lower()
    if response != 'y':
        print("👋 Deployment cancelled")
        sys.exit(0)
    
    # Deploy
    if deploy_to_fly():
        print("\n🎉 Deployment completed successfully!")
        print("🔗 Your app should be available at: https://form-translator-backend.fly.dev")
    else:
        print("\n❌ Deployment failed")
        sys.exit(1)

if __name__ == '__main__':
    main()


