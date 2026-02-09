# Gmail Inbox Cleaner Agent

An interactive tool to clean up your Gmail inbox efficiently and safely.

## Overview

Fetch recent emails from your Gmail inbox, group them by sender, and decide what to do with each sender's emails:
- **Keep** -> Leave emails untouched in your inbox  
- **Delete** -> Automatically save full emails locally as JSON, then move to Gmail trash

**Safety First**: All emails are saved locally *before* being trashed, ensuring you never lose data permanently.

## Features

Two interfaces available:
- **CLI Version** -> Simple, terminal-based interface for quick cleanups
- **Streamlit Web UI** -> Visual, button-driven interface with real-time progress tracking

## Requirements

- Python 3.11 or higher
- Gmail API enabled with OAuth credentials
- Dependencies listed in `requirements.txt`

## Installation
```bash
git clone git@github.com:RanaAashish/gmail-agent.git
cd /directory_path
pip install -r requirements.txt
```

## Setup

### Step 1: Enable Gmail API & Get Credentials

1. Visit the [Gmail API page](https://console.cloud.google.com/apis/library/gmail.googleapis.com)
2. Click **Enable** to activate the Gmail API
3. Navigate to **Credentials** -> **Create Credentials** -> **OAuth client ID**
4. Select **Desktop app** as the application type
5. Download the JSON credentials file
6. Rename it to `credentials.json`
7. Place it in the project root directory

### Step 2: First Run Authentication

Run either interface (see Usage below). On first launch:
- Your browser will open automatically
- Sign in with your Google account
- Grant the necessary permissions
- A `token.json` file will be created automatically

**⚠️ Security Note**: Never commit `token.json` or `credentials.json` to version control!

## Usage

### Streamlit Web UI (Recommended)
```bash
streamlit run app.py
```

**Workflow**:
1. Choose how many recent emails to fetch
2. Click **Start Cleanup**
3. Wait while emails are fetched and grouped (progress bar shown)
4. Review each sender -> click **Keep** or **Delete**
5. Click **Confirm choices & Run cleanup**
6. Watch real-time save and trash operations
7. View final summary (saved files count and trashed messages)

### CLI Version
```bash
python main.py
```
Follow the terminal prompts to clean your inbox.

## Output

Deleted emails are saved to `./saved_emails/` in JSON format, preserving:
- Email headers
- Body content
- Attachments metadata
- Sender information
- Timestamps

## Project Structure
```
gmail-inbox-cleaner/
├── app.py                 # Streamlit web interface
├── main.py                 # Terminal interface
├── credentials.json       # OAuth credentials (not in git)
├── token.json            # Auth token (not in git)
├── requirements.txt      # Python dependencies
├── saved_emails/         # Saved email archives
└── README.md            # This file
```

## Disclaimer

This tool moves emails to trash but does not permanently delete them. Emails remain in Gmail's trash for 30 days before permanent deletion. Always review your saved emails before emptying trash.