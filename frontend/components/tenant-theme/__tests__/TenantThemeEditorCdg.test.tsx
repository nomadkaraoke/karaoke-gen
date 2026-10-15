/**
 * @jest-environment jsdom
 *
 * Tenant theme editor, CD+G tab: only on portals that make CD+G files, edits map
 * onto the theme's cdg section, uploads are fitted for CD+G, and the previews on
 * that tab are the real CD+G up card + lyrics page.
 */
import React from "react"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const get = jest.fn()
const preview = jest.fn()
const save = jest.fn()
const uploadAsset = jest.fn()
let mockFeatures: Record<string, boolean> = { enable_cdg: true }

jest.mock("@/lib/api", () => ({
  tenantThemeApi: {
    get: (...a: unknown[]) => get(...a),
    preview: (...a: unknown[]) => preview(...a),
    save: (...a: unknown[]) => save(...a),
    uploadAsset: (...a: unknown[]) => uploadAsset(...a),
    rerenderOutdated: jest.fn(),
    outdatedJobs: async () => ({ theme_updated_at: null, job_ids: [] }),
  },
}))
jest.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast: jest.fn() }) }))
jest.mock("@/lib/tenant", () => ({ useTenant: () => ({ tenant: { name: "Vocal Star" }, features: mockFeatures }) }))
jest.mock("next-intl", () => ({
  useTranslations: () => (key: string, vars?: Record<string, string>) =>
    vars ? `${key}(${Object.values(vars).join(",")})` : key,
}))

import { TenantThemeEditorDialog } from "../TenantThemeEditorDialog"

const THEME = {
  intro: { font: "AvenirNext-Bold.ttf", title_color: "#ffffff" },
  karaoke: { primary_color: "0, 108, 249, 255", font_path: "AvenirNext-Bold.ttf" },
  end: {},
  cdg: {
    font_path: "Oswald-SemiBold.ttf", font_size: 18, title_color: "#ffff00", artist_color: "#ffff00",
    active_fill: "#006CF9", inactive_fill: "#ffff00", title_screen_background: "card.gif",
    title_separator_text: "MADE FAMOUS BY", title_separator_color: "#ffffff", title_block_top: 56,
    stroke_width: 0, active_stroke: "#000000", inactive_stroke: "#000000",
  },
}
const DATA = {
  theme_id: "vocalstar", style_params: THEME, images: ["card.gif", "bg.jpg"],
  fonts: ["AvenirNext-Bold.ttf", "Oswald-SemiBold.ttf"],
}
const IMAGES = {
  title_card: "data:image/jpeg;base64,AAA", karaoke_frame: "data:image/jpeg;base64,BBB",
  cdg_title_card: "data:image/png;base64,CCC", cdg_lyrics: "data:image/png;base64,DDD",
}

beforeEach(() => {
  jest.useFakeTimers()
  jest.clearAllMocks()
  mockFeatures = { enable_cdg: true }
  get.mockResolvedValue(DATA)
  preview.mockResolvedValue(IMAGES)
  save.mockImplementation(async (sp) => ({ ...DATA, style_params: sp }))
  uploadAsset.mockResolvedValue({ name: "Logo-cdg-1a2b3c4d.png" })
})
afterEach(() => jest.useRealTimers())

async function openCdgTab() {
  render(<TenantThemeEditorDialog open onClose={jest.fn()} />)
  await screen.findByText("tabs.titleCard")
  fireEvent.click(screen.getByRole("tab", { name: "tabs.cdg" }))
  await act(async () => { jest.advanceTimersByTime(800) })
}

function lastPreviewCall() {
  return preview.mock.calls[preview.mock.calls.length - 1]
}

it("has no CD+G tab on portals that don't make CD+G files", async () => {
  mockFeatures = { enable_cdg: false }
  render(<TenantThemeEditorDialog open onClose={jest.fn()} />)
  await screen.findByText("tabs.titleCard")
  expect(screen.queryByRole("tab", { name: "tabs.cdg" })).toBeNull()
})

it("has no CD+G tab when the theme has no CD+G section", async () => {
  get.mockResolvedValue({ ...DATA, style_params: { ...THEME, cdg: {} } })
  render(<TenantThemeEditorDialog open onClose={jest.fn()} />)
  await screen.findByText("tabs.titleCard")
  expect(screen.queryByRole("tab", { name: "tabs.cdg" })).toBeNull()
})

it("asks for and shows the real CD+G up card and lyrics previews on its tab", async () => {
  await openCdgTab()
  await waitFor(() => expect(lastPreviewCall()[3]).toEqual({ includeCdg: true }))
  expect(await screen.findByTestId("preview-cdg_title_card")).toHaveAttribute("src", IMAGES.cdg_title_card)
  expect(screen.getByTestId("preview-cdg_lyrics")).toHaveAttribute("src", IMAGES.cdg_lyrics)
  expect(screen.queryByTestId("preview-karaoke_frame")).toBeNull()
})

it("other tabs don't request the slower CD+G previews", async () => {
  render(<TenantThemeEditorDialog open onClose={jest.fn()} />)
  await screen.findByText("tabs.titleCard")
  await act(async () => { jest.advanceTimersByTime(800) })
  await waitFor(() => expect(preview).toHaveBeenCalled())
  expect(lastPreviewCall()[3]).toEqual({ includeCdg: false })
})

it("edits the cdg section: separator text, colours, sizes and position", async () => {
  await openCdgTab()
  fireEvent.change(screen.getByLabelText("cdgSeparatorText"), { target: { value: "AS MADE FAMOUS BY" } })
  fireEvent.change(screen.getByLabelText("sungColour", { selector: "input:not([type=color])" }), { target: { value: "#ff0000" } })
  fireEvent.change(screen.getByLabelText("cdgTextTop"), { target: { value: "70" } })
  fireEvent.change(screen.getByLabelText("cdgTitleSize"), { target: { value: "30" } })
  await act(async () => { jest.advanceTimersByTime(800) })
  await waitFor(() => expect(lastPreviewCall()[0].cdg.title_block_top).toBe(70))
  const cdg = lastPreviewCall()[0].cdg
  expect(cdg.title_separator_text).toBe("AS MADE FAMOUS BY")
  expect(cdg.active_fill).toBe("#ff0000")
  expect(cdg.title_font_size).toBe(30)
  expect(lastPreviewCall()[0].karaoke.primary_color).toBe("0, 108, 249, 255") // MP4 lyrics untouched
})

it("an outline colour applies to sung and unsung lyrics and only shows with an outline", async () => {
  await openCdgTab()
  expect(screen.queryByLabelText("cdgOutlineColour", { selector: "input:not([type=color])" })).toBeNull()
  fireEvent.change(screen.getByLabelText("cdgOutlineWidth"), { target: { value: "2" } })
  fireEvent.change(screen.getByLabelText("cdgOutlineColour", { selector: "input:not([type=color])" }), { target: { value: "#222222" } })
  fireEvent.click(screen.getByRole("button", { name: "save" }))
  await waitFor(() => expect(save).toHaveBeenCalled())
  const cdg = save.mock.calls[0][0].cdg
  expect(cdg.stroke_width).toBe(2)
  expect(cdg.active_stroke).toBe("#222222")
  expect(cdg.inactive_stroke).toBe("#222222")
})

it("the lyrics font can follow the main font or be a distinct CD+G font", async () => {
  await openCdgTab()
  const select = screen.getByLabelText("cdgLyricsFont")
  expect(select).toHaveValue("Oswald-SemiBold.ttf")
  fireEvent.change(select, { target: { value: "" } })
  fireEvent.click(screen.getByRole("tab", { name: "tabs.advanced" }))
  const json = JSON.parse((screen.getByLabelText("advancedJson") as HTMLTextAreaElement).value)
  expect(json.cdg.font_path).toBe("AvenirNext-Bold.ttf")
})

it("uploading a CD+G image asks the server to fit it and selects it", async () => {
  await openCdgTab()
  const file = new File(["img"], "Logo.jpg", { type: "image/jpeg" })
  await act(async () => {
    fireEvent.change(screen.getByLabelText("cdgUploadImage (cdgUpCardImage)"), { target: { files: [file] } })
  })
  expect(uploadAsset).toHaveBeenCalledWith(file, "cdg")
  await waitFor(() => expect(screen.getByLabelText("cdgUpCardImage")).toHaveValue("Logo-cdg-1a2b3c4d.png"))
})

it("CD+G previews keep the 300x216 shape with hard pixels", async () => {
  await openCdgTab()
  const img = await screen.findByTestId("preview-cdg_lyrics")
  expect(img.style.imageRendering).toBe("pixelated")
  expect((img.parentElement as HTMLElement).style.aspectRatio).toBe("300 / 216")
})
