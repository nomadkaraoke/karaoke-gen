import KaraokeHuntLetter from '@/components/karaokehunt/KaraokeHuntLetter';

// Letter to former KaraokeHunt app users who requested songs, linked from the outreach emails.
// The bare /karaokehunt URL locale-redirects here, so one link serves all 33 languages.
export default function KaraokeHuntLetterPage() {
  return (
    <KaraokeHuntLetter
      namespace="karaokehuntLetter"
      paragraphs={['apology', 'goodNews', 'credits', 'appRetired', 'thanks']}
    />
  );
}
