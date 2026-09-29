/**
 * @jest-environment jsdom
 *
 * Tenant theme editor: loads the theme, renders debounced server previews of the
 * draft, edits map onto style_params correctly, uploads add assets, Advanced JSON
 * is validated, and Save persists (applies to new jobs).
 */
import React from "react"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"

const get = jest.fn()
const preview = jest.fn()
const save = jest.fn()
const uploadAsset = jest.fn()
const toast = jest.fn()

jest.mock("@/lib/api", () => ({
  tenantThemeApi: {
    get: (...a: unknown[]) => get(...a),
    preview: (...a: unknown[]) => preview(...a),
    save: (...a: unknown[]) => save(...a),
    uploadAsset: (...a: unknown[]) => uploadAsset(...a),
  },
}))
jest.mock("@/hooks/use-toast", () => ({ useToast: () => ({ toast }) }))
jest.mock("@/lib/tenant", () => ({ useTenant: () => ({ tenant: { name: "Randy Vild" } }) }))
jest.mock("next-intl", () => ({
  useTranslations: () => (key: string, vars?: Record<string, string>) =>
    vars ? `${key}(${Object.values(vars).join(",")})` : key,
}))

import { TenantThemeEditorDialog } from "../TenantThemeEditorDialog"

const THEME = {
  intro: { background_image: "Sim.jpg", font: "AvenirNext-Bold.ttf", title_color: "#ffffff", artist_color: "#ffdf6b",
    title_region: "370,980,3100,350", artist_region: "370,1400,3100,450" },
  karaoke: { background_image: "Sim.jpg", primary_color: "61, 149, 197, 255", secondary_color: "255, 255, 255, 255",
    outline_color: "26, 58, 235, 255", font_path: "AvenirNext-Bold.ttf" },
  end: { extra_text: "THANK YOU FOR SINGING!", extra_text_color: "#ff7acc" },
  cdg: {},
}
const DATA = { theme_id: "randy-vild", style_params: THEME, images: ["Sim.jpg"], fonts: ["AvenirNext-Bold.ttf", "Montserrat-Bold.ttf"] }
const IMAGES = { title_card: "data:image/jpeg;base64,AAA", karaoke_frame: "data:image/jpeg;base64,BBB" }

beforeEach(() => {
  jest.useFakeTimers()
  jest.clearAllMocks()
  get.mockResolvedValue(DATA)
  preview.mockResolvedValue(IMAGES)
  save.mockImplementation(async (sp) => ({ ...DATA, style_params: sp }))
  uploadAsset.mockResolvedValue({ name: "NewBg-1a2b3c4d.jpg" })
})
afterEach(() => jest.useRealTimers())

async function openEditor() {
  const onClose = jest.fn()
  render(<TenantThemeEditorDialog open onClose={onClose} />)
  await screen.findByText("tabs.titleCard")
  return { onClose }
}

async function flushPreview() {
  await act(async () => { jest.advanceTimersByTime(800) })
}

it("loads the theme and renders the initial exact previews with the tenant's name as sample artist", async () => {
  await openEditor()
  await flushPreview()
  await waitFor(() => expect(preview).toHaveBeenCalledTimes(1))
  const [styles, sample] = preview.mock.calls[0]
  expect(styles).toEqual(THEME)
  expect(sample).toEqual({ artist: "Randy Vild", title: "sampleTitleDefault" })
  expect(await screen.findByTestId("preview-title_card")).toHaveAttribute("src", IMAGES.title_card)
  expect(screen.getByTestId("preview-karaoke_frame")).toHaveAttribute("src", IMAGES.karaoke_frame)
})

it("editing the sung colour converts hex to the karaoke 'r, g, b, a' format and re-previews (debounced)", async () => {
  await openEditor()
  await flushPreview()
  fireEvent.click(screen.getByRole("tab", { name: "tabs.karaoke" }))
  const input = screen.getByLabelText("sungColour", { selector: "input:not([type=color])" })
  fireEvent.change(input, { target: { value: "#ff0000" } })
  fireEvent.change(input, { target: { value: "#00ff00" } })
  await flushPreview()
  await waitFor(() => expect(preview).toHaveBeenCalledTimes(2)) // debounced: one call for both edits
  expect(preview.mock.calls[1][0].karaoke.primary_color).toBe("0, 255, 0, 255")
})

it("uploading a background stores the asset and selects it", async () => {
  await openEditor()
  const file = new File(["img"], "NewBg.jpg", { type: "image/jpeg" })
  await act(async () => {
    fireEvent.change(screen.getByLabelText("uploadImage (tabs.titleCard)"), { target: { files: [file] } })
  })
  expect(uploadAsset).toHaveBeenCalledWith(file)
  await waitFor(() => expect(screen.getByLabelText("backgroundImage")).toHaveValue("NewBg-1a2b3c4d.jpg"))
})

it("title position slider rewrites the region inside the 4K frame", async () => {
  await openEditor()
  fireEvent.change(screen.getByLabelText("positionFromTop(songTitle)"), { target: { value: "200" } })
  fireEvent.click(screen.getByRole("tab", { name: "tabs.advanced" }))
  const json = JSON.parse((screen.getByLabelText("advancedJson") as HTMLTextAreaElement).value)
  expect(json.intro.title_region).toBe("370,200,3100,350")
})

it("invalid Advanced JSON blocks saving; valid JSON applies to the draft", async () => {
  await openEditor()
  fireEvent.click(screen.getByRole("tab", { name: "tabs.advanced" }))
  const textarea = screen.getByLabelText("advancedJson")
  fireEvent.change(textarea, { target: { value: "{ nope" } })
  expect(screen.getByRole("button", { name: "save" })).toBeDisabled()
  fireEvent.change(textarea, { target: { value: JSON.stringify({ ...THEME, end: { extra_text: "BYE" } }) } })
  expect(screen.getByRole("button", { name: "save" })).toBeEnabled()
})

it("save sends the draft and reports success; discard reverts", async () => {
  await openEditor()
  fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
  fireEvent.change(screen.getByLabelText("closingMessage"), { target: { value: "SEE YOU NEXT TIME" } })
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "save" })) })
  expect(save).toHaveBeenCalledTimes(1)
  expect(save.mock.calls[0][0].end.extra_text).toBe("SEE YOU NEXT TIME")
  expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "savedTitle" }))
  expect(screen.getByRole("button", { name: "save" })).toBeDisabled() // nothing unsaved now

  fireEvent.change(screen.getByLabelText("closingMessage"), { target: { value: "OOPS" } })
  fireEvent.click(screen.getByRole("button", { name: "discard" }))
  expect(screen.getByLabelText("closingMessage")).toHaveValue("SEE YOU NEXT TIME")
})

it("asks before closing with unsaved changes", async () => {
  const { onClose } = await openEditor()
  fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
  fireEvent.change(screen.getByLabelText("closingMessage"), { target: { value: "CHANGED" } })
  const confirm = jest.spyOn(window, "confirm").mockReturnValue(false)
  fireEvent.click(screen.getByRole("button", { name: "close" }))
  expect(confirm).toHaveBeenCalled()
  expect(onClose).not.toHaveBeenCalled()
  confirm.mockReturnValue(true)
  fireEvent.click(screen.getByRole("button", { name: "close" }))
  expect(onClose).toHaveBeenCalled()
  confirm.mockRestore()
})

it("shows a preview error without breaking the editor", async () => {
  preview.mockRejectedValueOnce(new Error("Font 'X.ttf' was not found"))
  await openEditor()
  await flushPreview()
  expect(await screen.findByText(/Font 'X.ttf' was not found/)).toBeInTheDocument()
})

it("keeps edits made while a save is in flight", async () => {
  let resolveSave: (v: unknown) => void = () => {}
  save.mockImplementation((sp) => new Promise((r) => { resolveSave = () => r({ ...DATA, style_params: sp }) }))
  await openEditor()
  fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
  const msg = screen.getByLabelText("closingMessage")
  fireEvent.change(msg, { target: { value: "FIRST" } })
  fireEvent.click(screen.getByRole("button", { name: "save" }))
  fireEvent.change(msg, { target: { value: "SECOND (typed during save)" } })
  await act(async () => { resolveSave(undefined) })
  expect(screen.getByLabelText("closingMessage")).toHaveValue("SECOND (typed during save)")
  expect(screen.getByRole("button", { name: "save" })).toBeEnabled() // still unsaved
})

it("treats invalid Advanced JSON as unsaved work when closing", async () => {
  const { onClose } = await openEditor()
  fireEvent.click(screen.getByRole("tab", { name: "tabs.advanced" }))
  fireEvent.change(screen.getByLabelText("advancedJson"), { target: { value: "{ half typed" } })
  const confirm = jest.spyOn(window, "confirm").mockReturnValue(false)
  fireEvent.click(screen.getByRole("button", { name: "close" }))
  expect(confirm).toHaveBeenCalled()
  expect(onClose).not.toHaveBeenCalled()
  confirm.mockRestore()
})
