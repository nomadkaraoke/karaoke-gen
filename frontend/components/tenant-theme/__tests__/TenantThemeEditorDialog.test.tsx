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
const rerenderOutdated = jest.fn()
const toast = jest.fn()

jest.mock("@/lib/api", () => ({
  tenantThemeApi: {
    get: (...a: unknown[]) => get(...a),
    preview: (...a: unknown[]) => preview(...a),
    save: (...a: unknown[]) => save(...a),
    uploadAsset: (...a: unknown[]) => uploadAsset(...a),
    rerenderOutdated: (...a: unknown[]) => rerenderOutdated(...a),
    outdatedJobs: async () => ({ theme_updated_at: null, job_ids: [] }),
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

describe("after saving: what happens to existing tracks", () => {
  async function editAndSave() {
    await openEditor()
    fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
    fireEvent.change(screen.getByLabelText("closingMessage"), { target: { value: "BYE" } })
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "save" })) })
  }

  it("says in-progress tracks were updated and offers to re-render outdated finished ones", async () => {
    save.mockImplementation(async (sp) => ({ ...DATA, style_params: sp, refreshed_jobs: 3, outdated_job_ids: ["a", "b"] }))
    rerenderOutdated.mockResolvedValue({ started: ["a", "b"], failed: {} })
    await editAndSave()

    expect(screen.getByText("refreshedInProgress(3)")).toBeInTheDocument()
    expect(screen.getByText("outdatedFinished(2)")).toBeInTheDocument()
    await act(async () => { fireEvent.click(screen.getByTestId("theme-rerender-all")) })

    expect(rerenderOutdated).toHaveBeenCalledTimes(1)
    expect(screen.getByTestId("theme-rerender-started")).toHaveTextContent("rerenderAllStarted(2)")
    expect(screen.queryByTestId("theme-rerender-all")).not.toBeInTheDocument()
  })

  it("keeps the offer for tracks that couldn't be re-rendered", async () => {
    save.mockImplementation(async (sp) => ({ ...DATA, style_params: sp, refreshed_jobs: 0, outdated_job_ids: ["a", "b"] }))
    rerenderOutdated.mockResolvedValue({ started: ["a"], failed: { b: "published" } })
    await editAndSave()
    await act(async () => { fireEvent.click(screen.getByTestId("theme-rerender-all")) })

    expect(toast).toHaveBeenCalledWith(expect.objectContaining({ title: "rerenderAllSomeFailed(1)", variant: "destructive" }))
    expect(screen.getByText("outdatedFinished(1)")).toBeInTheDocument()
    expect(screen.queryByText("refreshedInProgress(0)")).not.toBeInTheDocument()
  })

  it("shows nothing extra when no existing track was affected", async () => {
    save.mockImplementation(async (sp) => ({ ...DATA, style_params: sp, refreshed_jobs: 0, outdated_job_ids: [] }))
    await editAndSave()
    expect(screen.queryByTestId("theme-save-result")).not.toBeInTheDocument()
  })
})

it("offers the tracks beyond the server's per-call cap again", async () => {
  save.mockImplementation(async (sp) => ({ ...DATA, style_params: sp, refreshed_jobs: 0, outdated_job_ids: ["a", "b", "c"] }))
  rerenderOutdated
    .mockResolvedValueOnce({ started: ["a"], failed: {}, remaining: ["b", "c"] })
    .mockResolvedValueOnce({ started: ["b", "c"], failed: {}, remaining: [] })
  await openEditor()
  fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
  fireEvent.change(screen.getByLabelText("closingMessage"), { target: { value: "BYE" } })
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "save" })) })

  await act(async () => { fireEvent.click(screen.getByTestId("theme-rerender-all")) })
  expect(screen.getByText("outdatedFinished(2)")).toBeInTheDocument()
  await act(async () => { fireEvent.click(screen.getByTestId("theme-rerender-all")) })
  expect(screen.getByTestId("theme-rerender-started")).toHaveTextContent("rerenderAllStarted(3)")
  expect(screen.queryByTestId("theme-rerender-all")).not.toBeInTheDocument()
})

describe("end screen", () => {
  const END_IMAGES = { ...IMAGES, end_screen: "data:image/jpeg;base64,CCC" }

  async function openEndTab() {
    preview.mockResolvedValue(END_IMAGES)
    await openEditor()
    fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
    await flushPreview()
  }

  function advancedJson() {
    fireEvent.click(screen.getByRole("tab", { name: "tabs.advanced" }))
    return JSON.parse((screen.getByLabelText("advancedJson") as HTMLTextAreaElement).value)
  }

  it("previews the end screen (instead of the title card) on its tab", async () => {
    await openEndTab()
    expect(await screen.findByTestId("preview-end_screen")).toHaveAttribute("src", END_IMAGES.end_screen)
    expect(screen.queryByTestId("preview-title_card")).toBeNull()
    expect(screen.getByTestId("preview-karaoke_frame")).toBeInTheDocument()
  })

  it("copies the title card's layout onto the end screen (smaller text like the title card)", async () => {
    await openEndTab()
    fireEvent.click(screen.getByTestId("end-copy-title-layout"))
    const json = advancedJson()
    expect(json.end).toMatchObject({
      title_region: THEME.intro.title_region,
      artist_region: THEME.intro.artist_region,
      title_color: "#ffffff",
      artist_color: "#ffdf6b",
      title_text_transform: null,
      artist_text_transform: null,
    })
    expect(json.end.extra_text).toBe("THANK YOU FOR SINGING!") // message text untouched
    expect(json.end.extra_text_region).toBe("370,570,3100,350") // moved just above the song title
  })

  it("end-screen sliders edit the end section, not the title card", async () => {
    await openEndTab()
    fireEvent.click(screen.getByLabelText("showSongTitle"))
    fireEvent.change(screen.getByLabelText("textSize(songTitle)"), { target: { value: "200" } })
    fireEvent.change(screen.getByLabelText("textSize(closingMessage)"), { target: { value: "250" } })
    const json = advancedJson()
    expect(json.end.title_region).toBe("370,980,3100,200") // turned on from the title card's position
    expect(json.end.extra_text_region).toBe("370,400,3100,250")
    expect(json.intro.title_region).toBe(THEME.intro.title_region)
  })

  it("hiding the song title clears its end-screen region", async () => {
    preview.mockResolvedValue(END_IMAGES)
    get.mockResolvedValue({ ...DATA, style_params: { ...THEME, end: { ...THEME.end, title_region: "370,900,3100,350" } } })
    await openEditor()
    fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
    expect(screen.getByLabelText("textSize(songTitle)")).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText("showSongTitle"))
    expect(screen.queryByLabelText("textSize(songTitle)")).toBeNull()
    expect(advancedJson().end.title_region).toBeNull()
  })

  it("a new closing message gets a region so it actually renders", async () => {
    get.mockResolvedValue({ ...DATA, style_params: { ...THEME, end: { extra_text: null } } })
    await openEndTab()
    fireEvent.change(screen.getByLabelText("closingMessage"), { target: { value: "BYE" } })
    expect(advancedJson().end).toMatchObject({ extra_text: "BYE", extra_text_region: "370,400,3100,400" })
  })
})

describe("leaving out the title card / end screen", () => {
  it("unticking the end screen saves enabled=false and shows it as not included", async () => {
    await openEditor()
    fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
    preview.mockResolvedValue({ ...IMAGES, end_screen: null })
    fireEvent.click(screen.getByTestId("end-enabled"))
    expect(screen.getByText("endScreenOff")).toBeInTheDocument()
    expect(screen.queryByLabelText("closingMessage")).toBeNull()
    await flushPreview()
    await waitFor(() => expect(preview.mock.calls.at(-1)[0].end.enabled).toBe(false))
    expect(await screen.findByTestId("preview-end_screen-omitted")).toHaveTextContent("screenOmitted")
    await act(async () => { fireEvent.click(screen.getByRole("button", { name: "save" })) })
    expect(save.mock.calls[0][0].end.enabled).toBe(false)
  })

  it("unticking the title card hides its settings; ticking it again restores them", async () => {
    await openEditor()
    fireEvent.click(screen.getByTestId("intro-enabled"))
    expect(screen.getByText("titleCardOff")).toBeInTheDocument()
    expect(screen.queryByLabelText("positionFromTop(songTitle)")).toBeNull()
    fireEvent.click(screen.getByTestId("intro-enabled"))
    expect(screen.getByLabelText("positionFromTop(songTitle)")).toBeInTheDocument()
  })

  it("treats a theme without an enabled key as including both screens", async () => {
    await openEditor()
    expect(screen.getByTestId("intro-enabled")).toBeChecked()
    fireEvent.click(screen.getByRole("tab", { name: "tabs.endScreen" }))
    expect(screen.getByTestId("end-enabled")).toBeChecked()
  })
})
