import { LocaleRedirect } from "@/components/LocaleRedirect"

// Preserve static params for local mode CLI paths
export async function generateStaticParams(): Promise<{ slug?: string[] }[]> {
  return [
    { slug: undefined },
    { slug: ['local', 'review'] },
    { slug: ['local', 'instrumental'] },
  ]
}

export default function JobsRedirect() {
  return <LocaleRedirect />
}
