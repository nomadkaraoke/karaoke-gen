import KaraokeHuntLetter from '@/components/karaokehunt/KaraokeHuntLetter';

// Letter to KaraokeHunt app users who signed up but never requested a song,
// linked from the Segment B outreach email. The bare /karaokehuntlist URL
// locale-redirects here, so one link serves all 33 languages.
export default function KaraokeHuntListLetterPage() {
  return (
    <KaraokeHuntLetter
      namespace="karaokehuntListLetter"
      paragraphs={['apology', 'goodNews', 'credits', 'thanks']}
    />
  );
}
