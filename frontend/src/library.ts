export type LibraryPaper = { id: string; title: string; kind: string; content_key?: string | null }

// Keep history IDs intact; collapse only equal extracted content in PDF pickers.
export function uniquePDFs<T extends LibraryPaper>(library: T[], selected: string[] = []): T[] {
  const groups = new Map<string, T>()
  for (const paper of library) {
    if (paper.kind !== 'pdf') continue
    const key = paper.content_key ? 'content:' + paper.content_key : 'id:' + paper.id
    if (!groups.has(key) || selected.includes(paper.id)) groups.set(key, paper)
  }
  return [...groups.values()]
}
