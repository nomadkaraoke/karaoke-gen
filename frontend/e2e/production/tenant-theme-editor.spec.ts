import { test, expect, Page } from "@playwright/test"

/**
 * Tenant self-service theme editor — Production E2E
 *
 * On a real tenant portal (default: randy-vild), as an allowed user (admin token):
 * 1. Open the user menu → "Theme & style"
 * 2. Both exact server-rendered previews (title card + lyrics frame) appear
 * 3. Change the sung-lyrics colour → the lyrics preview re-renders (image changes)
 *    while the title card stays the same
 * 4. Discard + close — leaves the tenant's saved theme untouched
 *
 * Run:
 *   KARAOKE_ADMIN_TOKEN=xxx npx playwright test e2e/production/tenant-theme-editor.spec.ts \
 *     --config=playwright.production.config.ts --reporter=list
 */

const TENANT_PORTAL_URL = process.env.TENANT_PORTAL_URL || "https://randy-vild.nomadkaraoke.com"

async function authenticate(page: Page, token: string) {
  await page.addInitScript((t) => localStorage.setItem("karaoke_access_token", t), token)
}

test.describe("Tenant theme editor", () => {
  test.describe.configure({ retries: 0 })

  test("preview updates live and discard leaves the theme unchanged", async ({ page }) => {
    const token = process.env.KARAOKE_ADMIN_TOKEN || ""
    test.skip(!token, "KARAOKE_ADMIN_TOKEN not set")
    test.setTimeout(180_000)

    await authenticate(page, token)
    await page.goto(`${TENANT_PORTAL_URL}/en/app`, { waitUntil: "networkidle" })

    // Consumer credits UI never appears on a tenant portal
    await expect(page.getByText(/credits available/i)).toHaveCount(0)

    await page.getByTestId("user-menu-trigger").click()
    await page.getByRole("menuitem", { name: "Theme & style" }).click()

    const editor = page.getByTestId("tenant-theme-editor")
    await expect(editor).toBeVisible()

    const title = editor.getByTestId("preview-title_card")
    const karaoke = editor.getByTestId("preview-karaoke_frame")
    await expect(title).toHaveAttribute("src", /^data:image\/jpeg;base64,/, { timeout: 60_000 })
    await expect(karaoke).toHaveAttribute("src", /^data:image\/jpeg;base64,/, { timeout: 60_000 })
    const titleBefore = await title.getAttribute("src")
    const karaokeBefore = await karaoke.getAttribute("src")

    await editor.getByRole("tab", { name: "Lyrics video" }).click()
    await editor.locator("#karaoke-primary_color").fill("#ff0000")

    await expect
      .poll(async () => karaoke.getAttribute("src"), { timeout: 60_000 })
      .not.toBe(karaokeBefore)
    expect(await title.getAttribute("src")).toBe(titleBefore)

    await editor.getByRole("button", { name: "Discard changes" }).click()
    await expect(editor.getByRole("button", { name: "Save theme" })).toBeDisabled()
    await editor.getByRole("button", { name: "Close" }).click()
    await expect(editor).toBeHidden()
  })
})
