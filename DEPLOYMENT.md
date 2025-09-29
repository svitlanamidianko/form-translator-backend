# Form Translator Backend - Deployment Guide

This guide will help you deploy your Flask backend to Fly.io for production use while keeping localhost development working.

## Prerequisites

1. **Install flyctl** (Fly.io CLI):
   ```bash
   brew install flyctl
   ```
   Or visit: https://fly.io/docs/hands-on/install-flyctl/

2. **Create a Fly.io account**:
   ```bash
   flyctl auth signup
   ```

3. **Set up environment variables**:
   - Copy `env.example` to `.env`
   - Fill in your actual API keys and secrets

## Environment Setup

### 1. Create your `.env` file:
```bash
cp env.example .env
```

### 2. Edit `.env` with your values:
```env
SECRET_KEY=your-actual-secret-key-here
DEBUG=true
FLASK_ENV=development
OPENAI_API_KEY=your-openai-api-key
GOOGLE_APPLICATION_CREDENTIALS=Form Translator DB IAM.json
```

### 3. Make sure your Google service account file is present:
- `Form Translator DB IAM.json` should be in the project root
- This file contains your Google Sheets API credentials

## Local Development

To run locally (as you've been doing):

```bash
# Activate virtual environment
source venv/bin/activate

# Install dependencies (if not already done)
pip install -r requirements.txt

# Run the app
python app.py
```

Your app will run on `http://localhost:7777` with debug mode enabled.

## Production Deployment to Fly.io

### Option 1: Use the deployment script (Recommended)

```bash
python deploy.py
```

This script will:
- Check all prerequisites
- Create the Fly.io app if needed
- Set up all secrets securely
- Deploy your application

### Option 2: Manual deployment

1. **Initialize Fly.io app**:
   ```bash
   flyctl launch --no-deploy
   ```

2. **Set secrets** (replace with your actual values):
   ```bash
   flyctl secrets set OPENAI_API_KEY="your-openai-api-key"
   flyctl secrets set SECRET_KEY="your-secret-key"
   ```

3. **Set Google credentials** (as a single secret):
   ```bash
   flyctl secrets set GOOGLE_APPLICATION_CREDENTIALS_JSON="$(cat 'Form Translator DB IAM.json')"
   ```

4. **Deploy**:
   ```bash
   flyctl deploy
   ```

## After Deployment

### Your app will be available at:
```
https://form-translator-backend.fly.dev
```

### Test your deployment:
```bash
curl https://form-translator-backend.fly.dev/
```

You should see a JSON response with app information.

### Monitor your app:
```bash
flyctl logs          # View logs
flyctl status        # Check app status
flyctl info          # Get app information
```

## Configuration Details

### Environment Differences

**Localhost (Development)**:
- Port: 7777
- Debug: True
- Credentials: From JSON file
- Environment: development

**Production (Fly.io)**:
- Port: 8080 (set by Fly.io)
- Debug: False
- Credentials: From environment variable
- Environment: production

### Fly.io Configuration (`fly.toml`)

- **Region**: Chicago (ord) - good for US coverage
- **Memory**: 1GB
- **CPU**: 1 shared CPU
- **Auto-scaling**: Enabled (can scale to 0 when not in use)
- **Health checks**: Enabled on `/` endpoint

## Troubleshooting

### Common Issues:

1. **"Credentials file not found"**:
   - Make sure `Form Translator DB IAM.json` is in the project root
   - Check that the file isn't corrupted

2. **"OpenAI API key not set"**:
   - Verify your `.env` file has the correct API key
   - For production, ensure secrets are set with `flyctl secrets set`

3. **App won't start on Fly.io**:
   - Check logs: `flyctl logs`
   - Verify all secrets are set: `flyctl secrets list`

4. **Google Sheets not working in production**:
   - Ensure the JSON credentials are properly set as a secret
   - Check that the service account has access to your sheet

### Useful Commands:

```bash
# View app logs
flyctl logs

# SSH into your app
flyctl ssh console

# Check app status
flyctl status

# List secrets (names only, not values)
flyctl secrets list

# Update a secret
flyctl secrets set SECRET_NAME="new-value"

# Redeploy
flyctl deploy
```

## Security Notes

- Never commit your `.env` file or service account JSON to version control
- Secrets are encrypted and secure in Fly.io
- The app runs with a non-root user for security
- HTTPS is enforced in production

## Next Steps

1. Test all endpoints in production
2. Update your frontend to use the production URL
3. Set up monitoring and alerts
4. Consider setting up a custom domain
5. Implement backup strategies for your data


