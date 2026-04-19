# Karaoke Generator Finalization Setup Guide

This guide explains how to configure the Modal web application to work the same way as your local CLI workflow, including YouTube upload, Dropbox sync, Discord notifications, and file organization.

## Overview

The finalization system now supports all the features from your CLI workflow:
- **YouTube Upload**: Automatically upload final videos to YouTube
- **Dropbox Sync**: Organize files in Dropbox with brand codes (NOMAD-XXXX)
- **Discord Notifications**: Send Discord webhook notifications for new uploads
- **Email Drafts**: Create email drafts for client delivery
- **Public Share**: Copy files to public sharing directories
- **Cloud Sync**: Sync public files to Google Drive

## Step 1: Deploy Updated Modal App

First, deploy the updated Modal app with the new finalization features:

```bash
modal deploy app.py
```

## Step 2: Configure Secrets

Set up your Modal secrets with the required environment variables:

```bash
# Update your existing env-vars secret with Discord webhook
modal secret create env-vars \
  ADMIN_TOKENS="your-admin-token-here" \
  AUTH_SECRET="your-auth-secret" \
  AUDIOSHAKE_API_TOKEN="your-audioshake-token" \
  GENIUS_API_TOKEN="your-genius-token"
```

## Step 3: Upload Configuration Files

Use the admin API endpoints to upload your configuration files. You'll need your YouTube client secrets, video description template, and email template.

### Upload YouTube Client Secrets

```bash
curl -X POST "https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api/admin/finalization/upload-config-file" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -F "file_type=youtube_secrets" \
  -F "config_file=@/path/to/your/youtube-client-secrets.json"
```

### Upload YouTube Description Template

```bash
curl -X POST "https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api/admin/finalization/upload-config-file" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -F "file_type=youtube_description" \
  -F "config_file=@/path/to/your/youtube-description.txt"
```

### Upload Email Template

```bash
curl -X POST "https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api/admin/finalization/upload-config-file" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -F "file_type=email_template" \
  -F "config_file=@/path/to/your/email-template.txt"
```

## Step 4: Configure rclone

Set up rclone configuration for Dropbox and Google Drive access. First, create your rclone.conf locally:

```bash
# Configure Dropbox remote
rclone config create andrewdropboxfull dropbox

# Configure Google Drive remote  
rclone config create googledrive drive
```

Then upload the rclone configuration:

```bash
# Get your rclone config
cat ~/.config/rclone/rclone.conf

# Upload it via the API
curl -X POST "https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api/admin/finalization/rclone-config" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -F "rclone_conf=@~/.config/rclone/rclone.conf"
```

## Step 5: Configure Finalization Settings

Update the finalization configuration to match your CLI workflow:

```bash
curl -X POST "https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api/admin/finalization/config" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "enable_youtube_upload": true,
    "enable_discord_notifications": true, 
    "enable_folder_organisation": true,
    "enable_public_share_copy": true,
    "enable_rclone_sync": true,
    "enable_email_drafts": true,
    "brand_prefix": "NOMAD",
    "organised_dir": "/output/organized",
    "organised_dir_rclone_root": "andrewdropboxfull:Tracks-Organized",
    "public_share_dir": "/output/public-share",
    "rclone_destination": "googledrive:Nomad Karaoke",
    "discord_webhook_url": "https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN"
  }'
```

## Step 6: Test Configuration

Test that everything is configured correctly:

```bash
curl -X POST "https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api/admin/finalization/test-config" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

This will return a detailed test report showing which features are working.

## Step 7: View Current Configuration

You can view your current configuration at any time:

```bash
curl -X GET "https://nomadkaraoke--karaoke-generator-webapp-api-endpoint.modal.run/api/admin/finalization/config" \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

## How It Works

Once configured, when jobs reach the finalization phase (Phase 3), the system will:

1. **Generate Final Videos**: Create all video formats (lossless 4K, lossy 4K, 720p)
2. **Create CDG/TXT Packages**: Generate karaoke machine files
3. **Upload to YouTube**: Upload the lossless MKV with your title/thumbnail
4. **Organize Files**: Move files to Dropbox with brand code (e.g., NOMAD-0948)
5. **Copy to Public Share**: Copy files to public sharing structure
6. **Sync to Google Drive**: Sync public files to Google Drive
7. **Send Discord Notification**: Post notification with YouTube link
8. **Create Email Draft**: Generate email draft for client delivery

## Troubleshooting

### YouTube Upload Issues
- Verify your client secrets file is valid JSON
- Check that the Google Cloud project has YouTube API enabled
- Test authentication by checking if tokens exist in `/tmp/`

### rclone Issues
- Test rclone commands manually in Modal: `modal run app.py::test_rclone`
- Verify remote names match exactly (case-sensitive)
- Check OAuth tokens are still valid

### Discord Notifications
- Verify webhook URL format: `https://discord.com/api/webhooks/...`
- Test webhook manually with curl

### File Organization
- Check volume permissions and disk space
- Verify brand prefix format and existing sequence numbers

## API Endpoints Reference

All admin finalization endpoints require admin authentication:

- `GET /api/admin/finalization/config` - View current configuration
- `POST /api/admin/finalization/config` - Update configuration
- `POST /api/admin/finalization/rclone-config` - Update rclone config
- `POST /api/admin/finalization/upload-config-file` - Upload config files
- `POST /api/admin/finalization/test-config` - Test configuration

## Example Configuration Files

### YouTube Description Template
```
Karaoke version of {youtube_url}

📱 Download karaoke files: {dropbox_url}

🎤 More karaoke tracks: https://nomadkaraoke.com
```

### Email Template
```
Hi there!

Your karaoke track is ready! 

🎵 YouTube: {youtube_url}
📁 Downloads: {dropbox_url}

Enjoy singing!
```

## Security Notes

- Configuration files are stored in Modal volumes and encrypted at rest
- rclone tokens are stored securely and not exposed in logs
- Admin endpoints require authentication tokens
- YouTube tokens are managed by Google's OAuth flow

## Next Steps

After setup is complete, your Modal karaoke generation will work exactly like your local CLI, automatically uploading to YouTube, organizing in Dropbox, and sending notifications. 