/**
 * Email Testing Utilities using testmail.app
 *
 * testmail.app provides namespace-based email addresses for e2e testing.
 * Emails arrive at: {namespace}.{tag}@inbox.testmail.app
 *
 * Setup:
 * 1. Create account at https://testmail.app
 * 2. Get API key and namespace from dashboard
 * 3. Set TESTMAIL_API_KEY and TESTMAIL_NAMESPACE environment variables
 *
 * Usage in tests:
 *   const emailHelper = await createEmailHelper();
 *   const inbox = await emailHelper.createInbox();
 *   // ... trigger email send to inbox.emailAddress ...
 *   const email = await emailHelper.waitForEmail(inbox.id);
 *   const magicLink = emailHelper.extractMagicLink(email);
 */

const TESTMAIL_API_KEY = process.env.TESTMAIL_API_KEY;
const TESTMAIL_NAMESPACE = process.env.TESTMAIL_NAMESPACE;
const TESTMAIL_API_URL = 'https://api.testmail.app/api/json';

/**
 * Inbox object compatible with existing test code
 */
export interface InboxDto {
  id: string;
  emailAddress: string;
}

/**
 * Email object compatible with existing test code
 */
export interface Email {
  subject?: string;
  body?: string;
  from?: string;
  timestamp?: number;
}

export interface EmailHelper {
  isAvailable: boolean;
  createInbox: () => Promise<InboxDto>;
  waitForEmail: (inboxId: string, timeout?: number) => Promise<Email>;
  extractMagicLink: (email: Email) => string | null;
  extractVerificationCode: (email: Email) => string | null;
  deleteInbox: (inboxId: string) => Promise<void>;
}

/**
 * testmail.app API response structure
 */
interface TestmailResponse {
  result: string;
  message?: string;
  count: number;
  limit: number;
  offset: number;
  emails: TestmailEmail[];
}

interface TestmailEmail {
  from: string;
  subject: string;
  html: string;
  text: string;
  timestamp: number;
  tag: string;
}

/**
 * Generate a unique tag for this test run
 */
function generateUniqueTag(): string {
  const timestamp = Date.now();
  const random = Math.random().toString(36).substring(2, 8);
  return `test-${timestamp}-${random}`;
}

/**
 * Creates an email testing helper.
 * Returns a helper with isAvailable=false if testmail.app env vars are not set.
 */
export async function createEmailHelper(): Promise<EmailHelper> {
  if (!TESTMAIL_API_KEY || !TESTMAIL_NAMESPACE) {
    console.warn('TESTMAIL_API_KEY or TESTMAIL_NAMESPACE not set - email testing disabled');
    return createDisabledHelper();
  }

  return {
    isAvailable: true,

    /**
     * Create a new "inbox" for testing.
     * In testmail.app, this just generates a unique tag - no API call needed.
     */
    async createInbox(): Promise<InboxDto> {
      const tag = generateUniqueTag();
      const emailAddress = `${TESTMAIL_NAMESPACE}.${tag}@inbox.testmail.app`;
      console.log(`Created test inbox: ${emailAddress}`);
      return { id: tag, emailAddress };
    },

    /**
     * Wait for an email to arrive in the inbox
     * @param inboxId - The tag to check (returned from createInbox as id)
     * @param timeout - Timeout in ms (default: 60000 = 1 minute)
     */
    async waitForEmail(inboxId: string, timeout = 60000): Promise<Email> {
      console.log(`Waiting for email with tag ${inboxId}...`);

      // testmail.app livequery timeout is in seconds, max ~60s per request
      // For longer timeouts, we poll multiple times
      const pollInterval = Math.min(timeout, 30000); // 30s max per poll
      const startTime = Date.now();

      while (Date.now() - startTime < timeout) {
        const remainingTime = timeout - (Date.now() - startTime);
        const waitTime = Math.min(pollInterval, remainingTime);
        const waitSeconds = Math.ceil(waitTime / 1000);

        const url = new URL(TESTMAIL_API_URL);
        url.searchParams.set('apikey', TESTMAIL_API_KEY!);
        url.searchParams.set('namespace', TESTMAIL_NAMESPACE!);
        url.searchParams.set('tag', inboxId);
        url.searchParams.set('livequery', 'true');
        url.searchParams.set('wait', waitSeconds.toString());

        try {
          const response = await fetch(url.toString());

          if (!response.ok) {
            throw new Error(`testmail.app API error: ${response.status} ${response.statusText}`);
          }

          const data: TestmailResponse = await response.json();

          if (data.result !== 'success') {
            throw new Error(`testmail.app API error: ${data.message || 'Unknown error'}`);
          }

          if (data.emails && data.emails.length > 0) {
            // Return the most recent email
            const latestEmail = data.emails.sort((a, b) => b.timestamp - a.timestamp)[0];
            console.log(`Received email: ${latestEmail.subject}`);

            // Convert to our Email format (use html as body, fallback to text)
            return {
              subject: latestEmail.subject,
              body: latestEmail.html || latestEmail.text,
              from: latestEmail.from,
              timestamp: latestEmail.timestamp,
            };
          }
        } catch (error) {
          // If it's a timeout or network error, continue polling
          if (Date.now() - startTime < timeout) {
            console.log(`Polling for email... (${Math.round((Date.now() - startTime) / 1000)}s elapsed)`);
            continue;
          }
          throw error;
        }
      }

      throw new Error(`Timed out waiting for email after ${timeout}ms`);
    },

    /**
     * Extract magic link URL from email body
     */
    extractMagicLink(email: Email): string | null {
      const body = email.body || '';

      // Debug: Log email structure
      console.log('Email subject:', email.subject);
      console.log('Email body length:', body.length);
      console.log('Email body preview (first 500 chars):', body.substring(0, 500));

      // Look for common magic link patterns
      const patterns = [
        // gen.nomadkaraoke.com verify link
        /https:\/\/gen\.nomadkaraoke\.com\/auth\/verify\?token=[a-zA-Z0-9_-]+/,
        // api.nomadkaraoke.com verify link
        /https:\/\/api\.nomadkaraoke\.com\/api\/users\/auth\/verify\?token=[a-zA-Z0-9_-]+/,
        // Generic magic link patterns - allow URL-encoded characters
        /https?:\/\/[^\s<>"]+\/auth\/verify[^\s<>"]*/,
        /https?:\/\/[^\s<>"]+verify\?token=[a-zA-Z0-9_%-]+/,
        /https?:\/\/[^\s<>"]+token=[a-zA-Z0-9_%-]+/,
      ];

      for (const pattern of patterns) {
        const match = body.match(pattern);
        if (match) {
          console.log(`Found magic link with pattern ${pattern}: ${match[0]}`);
          return match[0];
        }
      }

      // Also check HTML links
      if (email.body) {
        const htmlLinkPattern = /href=["']([^"']*(?:verify|token)[^"']*)["']/i;
        const htmlMatch = email.body.match(htmlLinkPattern);
        if (htmlMatch) {
          console.log(`Found magic link in HTML href: ${htmlMatch[1]}`);
          return htmlMatch[1];
        }
      }

      // Debug: Log all URLs found in the email
      const allUrls = body.match(/https?:\/\/[^\s<>"]+/g) || [];
      console.log('All URLs found in email:', allUrls);

      console.warn('No magic link found in email');
      return null;
    },

    /**
     * Extract verification code from email body
     */
    extractVerificationCode(email: Email): string | null {
      const body = email.body || '';

      // Look for 6-digit codes
      const codePattern = /\b(\d{6})\b/;
      const match = body.match(codePattern);

      if (match) {
        console.log(`Found verification code: ${match[1]}`);
        return match[1];
      }

      console.warn('No verification code found in email');
      return null;
    },

    /**
     * Delete an inbox after testing.
     * In testmail.app, emails auto-expire - this is a no-op.
     */
    async deleteInbox(inboxId: string): Promise<void> {
      // testmail.app emails auto-expire, no cleanup needed
      console.log(`Inbox ${inboxId} cleanup skipped (testmail.app auto-expires)`);
    },
  };
}

/**
 * Creates a disabled helper for when testmail.app is not configured
 */
function createDisabledHelper(): EmailHelper {
  const notAvailable = () => {
    throw new Error('Email testing not available - set TESTMAIL_API_KEY and TESTMAIL_NAMESPACE');
  };

  return {
    isAvailable: false,
    createInbox: notAvailable,
    waitForEmail: notAvailable,
    extractMagicLink: () => null,
    extractVerificationCode: () => null,
    deleteInbox: notAvailable,
  };
}

/**
 * Check if email testing is available
 */
export function isEmailTestingAvailable(): boolean {
  return !!(TESTMAIL_API_KEY && TESTMAIL_NAMESPACE);
}
