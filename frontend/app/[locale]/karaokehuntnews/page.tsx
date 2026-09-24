import KaraokeHuntLetter from '@/components/karaokehunt/KaraokeHuntLetter';

// Letter to KaraokeHunt mailing-list subscribers (signed up on karaokehunt.com to hear
// what was being built, never used the app), linked from the launch email.
// The bare /karaokehuntnews URL locale-redirects here, so one link serves all 33 languages.
export default function KaraokeHuntNewsLetterPage() {
  return (
    <KaraokeHuntLetter
      namespace="karaokehuntNewsLetter"
      paragraphs={['apology', 'stillHere', 'whatItIs', 'credits', 'thatsIt']}
      link={{ href: 'https://nomadkaraoke.com/r/thankyou50', label: 'nomadkaraoke.com/r/thankyou50' }}
      hasCreditsAfter={false}
    />
  );
}
