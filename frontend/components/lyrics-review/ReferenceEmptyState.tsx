'use client'

import { useTranslations } from 'next-intl'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@/components/ui/tabs'
import { Info } from 'lucide-react'
import { SearchLyricsResponse } from '@/lib/lyrics-review/types'
import SearchLyricsTab from './modals/SearchLyricsTab'
import PasteLyricsTab from './modals/PasteLyricsTab'

interface ReferenceEmptyStateProps {
  defaultArtist: string
  defaultTitle: string
  onAdd: (source: string, lyrics: string) => Promise<void>
  onSearch: (artist: string, title: string, forceSources: string[]) => Promise<SearchLyricsResponse>
}

export default function ReferenceEmptyState({
  defaultArtist,
  defaultTitle,
  onAdd,
  onSearch,
}: ReferenceEmptyStateProps) {
  const t = useTranslations('lyricsReview.referenceEmpty')
  // onClose is a no-op here — it's inline, not a modal
  const handleClose = () => {}

  return (
    <div className="space-y-4 p-2">
      <Alert>
        <Info className="h-4 w-4" />
        <AlertDescription>
          {t('noReferenceFound')}
        </AlertDescription>
      </Alert>

      <Tabs defaultValue="search">
        <TabsList className="w-full">
          <TabsTrigger value="search" className="flex-1">
            {t('search')}
          </TabsTrigger>
          <TabsTrigger value="paste" className="flex-1">
            {t('paste')}
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
    </div>
  )
}
