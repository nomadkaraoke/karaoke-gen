import { test, expect } from '@playwright/test';
import { setupApiFixtures, setAuthToken, clearAuthToken } from '../fixtures/test-helper';

/**
 * Regression Tests - Requests board deep link (?request=<id>)
 *
 * The YouTube comment bot replies with a link straight to one request. The board
 * highlights it, signs the visitor in with a purpose that returns them to it, and
 * on return (&upvote=1) casts their upvote once.
 */

const REQ_ID = 'req-paper-moons';

function boardBody(yourVote: number | null = null, votes = 0) {
  const row = (id: string, title: string, artist: string, vote_count: number, your_vote: number | null = null) => ({
    id, title, artist, status: 'open', source: id === REQ_ID ? 'youtube_comment' : 'human',
    vote_count, created_at: '2026-10-21T00:00:00+00:00', youtube_url: null, was_corrected: false, your_vote,
  });
  return {
    requests: [
      row('req-other-1', 'Tin Sky', 'Glass Harbor', 5),
      row('req-other-2', 'Velvet Static', 'Neon Orchard', 3),
      row(REQ_ID, 'Paper Moons', 'The Lanterns', votes, yourVote),
    ],
    published: [],
    voted_today: yourVote !== null,
  };
}

test.describe('Requests board deep link', () => {
  test('signed out: highlights the request and signs in with its purpose', async ({ page }) => {
    await clearAuthToken(page);
    await setupApiFixtures(page, {
      mocks: [{ method: 'GET', path: '/api/requests-board/requests', response: { body: boardBody() } }],
    });
    let magicLinkBody: Record<string, unknown> | null = null;
    await page.route('**/api/users/auth/magic-link', async (route) => {
      magicLinkBody = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', message: 'sent' }) });
    });

    await page.goto(`/en/requests?request=${REQ_ID}&utm_source=youtube&utm_medium=comment_reply`);

    await expect(page.getByTestId('deep-link-banner')).toContainText('Paper Moons');
    await expect(page.getByTestId('deep-link-banner')).toContainText('Sign in');
    await expect(page.locator(`#request-${REQ_ID}`)).toHaveAttribute('data-highlighted', 'true');

    await page.locator('input[type="email"]').fill('fan@example.com');
    await page.getByRole('button', { name: /sign-in link/i }).click();
    await expect.poll(() => magicLinkBody?.purpose).toBe(`requests_board:${REQ_ID}`);
  });

  test('back from the magic link: upvotes once and drops the param', async ({ page }) => {
    await setAuthToken(page, 'mock-user-token');
    let voted = false;
    const votes: string[] = [];
    await setupApiFixtures(page, {
      mocks: [{ method: 'GET', path: '/api/users/me', response: { body: { user: { email: 'fan@example.com', role: 'user', credits: 0, is_active: true } } } }],
    });
    await page.route('**/api/requests-board/requests', async (route) => {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(boardBody(voted ? 1 : null, voted ? 1 : 0)) });
    });
    await page.route(`**/api/requests-board/requests/${REQ_ID}/vote`, async (route) => {
      votes.push(route.request().postDataJSON().direction);
      voted = true;
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ id: REQ_ID }) });
    });

    await page.goto(`/en/requests?request=${REQ_ID}&upvote=1`);

    await expect(page.getByTestId('deep-link-banner')).toContainText('You upvoted');
    expect(votes).toEqual(['up']);
    await expect.poll(() => new URL(page.url()).searchParams.get('upvote')).toBeNull();
    expect(new URL(page.url()).searchParams.get('request')).toBe(REQ_ID);

    await page.reload();
    await expect(page.getByTestId('deep-link-banner')).toContainText('You upvoted');
    expect(votes).toEqual(['up']);
  });

  test('already upvoted today: does not toggle the vote off', async ({ page }) => {
    await setAuthToken(page, 'mock-user-token');
    const votes: string[] = [];
    await setupApiFixtures(page, {
      mocks: [
        { method: 'GET', path: '/api/users/me', response: { body: { user: { email: 'fan@example.com', role: 'user', credits: 0, is_active: true } } } },
        { method: 'GET', path: '/api/requests-board/requests', response: { body: boardBody(1, 1) } },
      ],
    });
    await page.route(`**/api/requests-board/requests/${REQ_ID}/vote`, async (route) => {
      votes.push('called');
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await page.goto(`/en/requests?request=${REQ_ID}&upvote=1`);
    await expect(page.getByTestId('deep-link-banner')).toContainText('You upvoted');
    expect(votes).toEqual([]);
  });
});
