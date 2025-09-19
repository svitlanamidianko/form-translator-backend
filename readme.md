# Form Translator Backend

## Setup Instructions

### 1. Environment Setup
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Environment Variables
Create a `.env` file in the project root with:
```
OPENAI_API_KEY=your_openai_api_key_here
SECRET_KEY=your_flask_secret_key_here
DEBUG=False
```

### 3. Google Sheets Credentials
- Place your Google Service Account JSON file in the project root as `Form Translator DB IAM.json`
- Or set the `GOOGLE_APPLICATION_CREDENTIALS` environment variable to point to your credentials file

### 4. Run the Application
```bash
python app.py
```

## Security Notes
- Never commit credentials files to Git
- Use environment variables for sensitive data
- The `.gitignore` file excludes all credential files

