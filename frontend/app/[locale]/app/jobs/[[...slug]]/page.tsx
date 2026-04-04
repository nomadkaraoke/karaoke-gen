import { JobRouterClient } from "./client"

// Required for static export with catch-all routes
// Returns params array for paths to pre-render at build time
export async function generateStaticParams(): Promise<{ slug?: string[] }[]> {
  return [
    { slug: undefined }, // /app/jobs/
    // Local mode paths - pre-rendered for karaoke-gen CLI local review
    { slug: ['local', 'review'] },       // /app/jobs/local/review
    { slug: ['local', 'instrumental'] }, // /app/jobs/local/instrumental
  ]
}

export default function JobsPage() {
  return <JobRouterClient />
}
