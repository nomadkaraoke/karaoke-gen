'use client'

import { useState } from 'react'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { SearchLyricsResponse } from '@/lib/lyrics-review/types'
import SearchLyricsTab from './SearchLyricsTab'
import PasteLyricsTab from './PasteLyricsTab'

interface AddLyricsModalProps {
  open: boolean
  onClose: () => void
  onAdd: (source: string, lyrics: string) => Promise<void>
  onSearch: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
  defaultArtist: string
  defaultTitle: string
}

export default function AddLyricsModal({
  open,
  onClose,
  onAdd,
  onSearch,
  defaultArtist,
  defaultTitle,
}: AddLyricsModalProps) {
  const [activeTab, setActiveTab] = useState('search')

  const handleClose = () => {
    setActiveTab('search')
    onClose()
  }

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && handleClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Add Reference Lyrics</DialogTitle>
        </DialogHeader>

        <Tabs value={activeTab} onValueChange={setActiveTab}>
          <TabsList className="w-full">
            <TabsTrigger value="search" className="flex-1">
              Search
            </TabsTrigger>
            <TabsTrigger value="paste" className="flex-1">
              Paste
            </TabsTrigger>
          </TabsList>

          <TabsContent value="search">
            <SearchLyricsTab
              defaultArtist={defaultArtist}
              defaultTitle={defaultTitle}
              onSearch={onSearch}
              onClose={handleClose}
            />
          </TabsContent>

          <TabsContent value="paste">
            <PasteLyricsTab onAdd={onAdd} onClose={handleClose} />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
