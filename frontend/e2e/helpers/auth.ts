/**
 * Shared authentication utilities for E2E tests
 */

import { Page, APIRequestContext } from '@playwright/test';
import { STORAGE_KEYS, URLS, TIMEOUTS } from './constants';

/**
 * Set the auth token in localStorage before page navigation.
 * Must be called BEFORE navigating to the page.
 */
export async function setAuthToken(page: Page, token: string): Promise<void> {
  await page.addInitScript((t) => {
    localStorage.setItem('karaoke_access_token', t);
  }, token);
}

/**
 * Clear the auth token from localStorage.
 * Useful for testing unauthenticated flows.
 */
export async function clearAuthToken(page: Page): Promise<void> {
  await page.evaluate(() => {
    localStorage.removeItem('karaoke_access_token');
  });
}

/**
 * Get the current auth token from localStorage.
 */
export async function getPageAuthToken(page: Page): Promise<string | null> {
  return await page.evaluate(() => {
    return localStorage.getItem('karaoke_access_token');
  });
}

/**
 * Get the auth token from environment variable.
 */
export function getEnvAuthToken(): string | undefined {
  return process.env.KARAOKE_ACCESS_TOKEN;
}

/**
 * Authenticate a page using environment token if available.
 * Returns true if authenticated, false otherwise.
 */
export async function authenticatePage(page: Page): Promise<boolean> {
  const token = getEnvAuthToken();
  if (!token) {
    console.warn('No KARAOKE_ACCESS_TOKEN set - authentication skipped');
    return false;
  }

  await setAuthToken(page, token);
  return true;
}

/**
 * Verify that the current token is valid by making an API call.
 */
export async function verifyToken(
  request: APIRequestContext,
  token: string,
  apiUrl: string = URLS.production.api
): Promise<{ valid: boolean; user?: any }> {
  const response = await request.get(`${apiUrl}/api/users/me`, {
    headers: { Authorization: `Bearer ${token}` },
    timeout: TIMEOUTS.apiCall,
  });

  if (response.ok()) {
    const user = await response.json();
    return { valid: true, user };
  }

  return { valid: false };
}

/**
 * Get user info for the current token.
 */
export async function getUserInfo(
  request: APIRequestContext,
  token: string,
  apiUrl: string = URLS.production.api
): Promise<{
  email: string;
  credits: number;
} | null> {
  const response = await request.get(`${apiUrl}/api/users/me`, {
    headers: { Authorization: `Bearer ${token}` },
    timeout: TIMEOUTS.apiCall,
  });

  if (response.ok()) {
    const data = await response.json();
    return {
      email: data.email,
      credits: data.credits,
    };
  }

  return null;
}
