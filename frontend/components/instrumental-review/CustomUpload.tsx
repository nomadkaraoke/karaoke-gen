"use client"

import { useRef } from "react"
import { Upload } from "lucide-react"
import { Button } from "@/components/ui/button"

interface CustomUploadProps {
  onUpload: (file: File) => Promise<void>
  isUploading: boolean
  acceptedFormats?: string[]
}

export function CustomUpload({
  onUpload,
  isUploading,
  acceptedFormats = [".flac", ".mp3", ".wav", ".m4a", ".ogg"],
}: CustomUploadProps) {
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    try {
      await onUpload(file)
    } finally {
      // Reset input so same file can be selected again
      if (inputRef.current) {
        inputRef.current.value = ""
      }
    }
  }

  return (
    <Button
      variant="secondary"
      size="sm"
      disabled={isUploading}
      className="relative h-8 text-xs"
      asChild
    >
      <label className="cursor-pointer">
        <Upload className="w-3.5 h-3.5 mr-1.5" />
        {isUploading ? "Uploading..." : "Upload"}
        <input
          ref={inputRef}
          type="file"
          accept={acceptedFormats.join(",")}
          onChange={handleFileChange}
          className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
          disabled={isUploading}
        />
      </label>
    </Button>
  )
}
