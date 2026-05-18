# Stripe Setup Guide

This guide walks you through setting up Stripe for the Nomad Karaoke payment system.

## Prerequisites

- A Stripe account (sign up at https://stripe.com)
- Access to Google Cloud Secret Manager (for secure credential storage)
- The Nomad Karaoke backend deployed and running

## 1. Get Your Stripe API Keys

1. Log in to your Stripe Dashboard: https://dashboard.stripe.com
2. Go to **Developers** > **API keys**
3. You'll need two keys:
   - **Publishable key** (starts with `pk_live_` or `pk_test_`)
   - **Secret key** (starts with `sk_live_` or `sk_test_`)

> **Important**: Never commit your secret key to version control. Always use environment variables or Secret Manager.

## 2. Store Keys in Google Cloud Secret Manager

The secret containers are already created via Pulumi (in `infrastructure/__main__.py`). You just need to add the secret values:

```bash
# Store the Stripe secret key
echo -n "sk_live_your_key_here" | gcloud secrets versions add stripe-secret-key --data-file=-

# Store the webhook secret (after step 3)
echo -n "whsec_your_webhook_secret" | gcloud secrets versions add stripe-webhook-secret --data-file=-
```

> **Note**: If running for the first time after Pulumi creates the secrets, use `gcloud secrets versions add` (not `gcloud secrets create`).

## 3. Set Up Webhook Endpoint

Webhooks notify your backend when payments complete.

### In Stripe Dashboard:

1. Go to **Developers** > **Webhooks**
2. Click **Add endpoint**
3. Enter your endpoint URL: `https://api.nomadkaraoke.com/api/users/webhooks/stripe`
4. Select events to listen to:
   - `checkout.session.completed` (required - adds credits)
   - `checkout.session.expired` (optional - logging)
   - `payment_intent.payment_failed` (optional - logging)
5. Click **Add endpoint**
6. Copy the **Signing secret** (starts with `whsec_`)
7. Store it in Secret Manager (see step 2)

## 4. Configure Environment Variables

Set these environment variables for your backend:

```bash
# Required
STRIPE_SECRET_KEY=sk_live_your_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret

# Optional (defaults shown)
FRONTEND_URL=https://gen.nomadkaraoke.com
# BUY_URL defaults to FRONTEND_URL after site consolidation

# Payment Method Configuration (enables Google Pay, Apple Pay, Link)
# Get this from: Dashboard > Settings > Payment methods > [Your Config] > Copy ID
STRIPE_PAYMENT_METHOD_CONFIG=pmc_your_config_id_here
```

### Payment Method Configuration

To enable Google Pay, Apple Pay, Link, and other wallet payment methods in your checkout:

1. Go to **Stripe Dashboard** > **Settings** > **Payment methods**
2. Create or select a configuration (e.g., "Default")
3. Enable the payment methods you want (Google Pay, Apple Pay, Link, etc.)
4. Copy the configuration ID (starts with `pmc_`)
5. Set it as `STRIPE_PAYMENT_METHOD_CONFIG`

Without this setting, checkout sessions will only show basic payment methods (cards, some regional methods). With it, customers will see Google Pay, Apple Pay, and Link options when available on their device.

### For Cloud Run deployment:

The CI workflow (`.github/workflows/ci.yml`) automatically injects these secrets during deployment. No manual `gcloud` commands needed - just add the secret values to Secret Manager (step 2) and the next backend deployment will pick them up.

## 5. Configure Email Service (Postmark)

For sending magic link and purchase confirmation emails:

1. Sign up for Postmark: https://postmarkapp.com
2. Verify a sender signature or domain for `nomadkaraoke.com`
3. Create a server and copy its Server API Token
4. Store it in Secret Manager (secret container created by Pulumi):

```bash
echo -n "your_postmark_server_token" | gcloud secrets versions add postmark-server-token --data-file=-
```

5. The environment variables are configured in the Cloud Run deployment. Defaults:
   - `EMAIL_FROM=gen@nomadkaraoke.com`
   - `EMAIL_FROM_NAME=Nomad Karaoke`

## 6. Test the Integration

### Test Mode

Use test keys (starting with `pk_test_` and `sk_test_`) for testing:

1. Use Stripe's test card: `4242 4242 4242 4242`
2. Any future expiration date
3. Any 3-digit CVC

### Verify Webhook Delivery

1. In Stripe Dashboard, go to **Developers** > **Webhooks**
2. Click on your endpoint
3. View recent webhook attempts and their status
4. Use the "Send test webhook" feature to verify connectivity

## Credit Packages

The system comes with these default packages:

| Package | Credits | Price | Per Credit |
|---------|---------|-------|------------|
| 1 Credit | 1 | $5.00 | $5.00 |
| 3 Credits | 3 | $12.00 | $4.00 (20% off) |
| 5 Credits | 5 | $17.50 | $3.50 (30% off) |
| 10 Credits | 10 | $30.00 | $3.00 (40% off) |

To modify packages, edit `backend/services/stripe_service.py`:

```python
CREDIT_PACKAGES = {
    "1_credit": {
        "credits": 1,
        "price_cents": 500,
        "name": "1 Karaoke Credit",
        "description": "Create 1 professional karaoke video",
    },
    # Add more packages...
}
```

## Admin Features

### Issue Free Credits

Admins can issue free credits via the API:

```bash
curl -X POST https://api.nomadkaraoke.com/api/users/admin/credits \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user@example.com",
    "amount": 5,
    "reason": "Beta tester reward"
  }'
```

### View Users

```bash
curl https://api.nomadkaraoke.com/api/users/admin/users \
  -H "Authorization: Bearer YOUR_ADMIN_TOKEN"
```

## Troubleshooting

### Webhook Not Receiving Events

1. Verify the endpoint URL is correct and publicly accessible
2. Check the webhook signing secret matches
3. Ensure your server returns 2xx status codes
4. Check Cloud Run logs for errors

### Credits Not Adding

1. Verify `checkout.session.completed` event is being received
2. Check that `metadata` contains `user_email` and `credits`
3. Review backend logs for errors in `stripe_webhook` handler

### Email Not Sending

1. Verify Postmark server token is valid
2. Check sender email/domain is verified in Postmark
3. Review Postmark activity logs (Messages → Outbound) for suppressions or hard bounces

## Security Considerations

1. **Never expose secret keys** - Use environment variables or Secret Manager
2. **Always verify webhook signatures** - The code does this automatically
3. **Use HTTPS** - All Stripe communication must be over HTTPS
4. **Monitor webhook logs** - Watch for failed deliveries or suspicious activity
5. **Set up alerts** - Configure Stripe to alert on unusual activity

## Going Live Checklist

- [ ] Switch from test to live API keys
- [ ] Update webhook endpoint to use live signing secret
- [ ] Verify email sending works with real addresses
- [ ] Test a real purchase with a small amount
- [ ] Set up Stripe Radar for fraud protection
- [ ] Configure Stripe tax settings if applicable
- [ ] Review and enable appropriate Stripe email receipts
