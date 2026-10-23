export type CitationPaper = {
  id: string; title: string; authors: string[]; year: number | null;
  publication_date?: string | null; doi?: string | null; arxiv_id?: string | null; url: string; sources: string[];
}
const clean = (value: string) => value.replace(/\s+/g, ' ').trim()
const tex = (value: string) => clean(value).replace(/[\\{}%&_$#~^]/g, char => ({
  '\\': '{\\textbackslash{}}', '{': '\\{', '}': '\\}', '%': '\\%', '&': '\\&', '_': '\\_', '$': '\\$', '#': '\\#', '~': '{\\textasciitilde{}}', '^': '{\\textasciicircum{}}',
}[char]!))
function hash(value: string) {
  let n = 2166136261
  for (const char of value) n = Math.imul(n ^ char.charCodeAt(0), 16777619)
  return (n >>> 0).toString(16)
}
export function makeCitation(paper: CitationPaper, format: 'text' | 'bibtex', accessed: string) {
  const authors = paper.authors.map(clean).filter(Boolean)
  const title = clean(paper.title)
  // A source DOI may identify a published version rather than the arXiv version.
  const arxiv = paper.arxiv_id || (paper.sources.includes('arxiv') ? paper.url.match(/arxiv\.org\/abs\/([^?#]+)/)?.[1] : undefined)
  const url = arxiv ? `https://arxiv.org/abs/${arxiv}` : paper.url
  if (format === 'text') {
    const names = authors.length > 3 ? authors.slice(0, 3).join(', ') + (authors.every(a => /[\u3400-\u9fff]/.test(a)) ? ', 等' : ', et al.') : authors.join(', ')
    const date = paper.publication_date || (paper.year ? String(paper.year) : '')
    return `${names ? names + '. ' : ''}${title}[${arxiv ? 'EB' : 'Z'}/OL]. ${arxiv ? 'arXiv, ' : ''}${date ? date : ''}[${accessed}]. ${url}.${!arxiv && paper.doi ? ' DOI: ' + paper.doi + '.' : ''}`
  }
  const fields: [string, string][] = [['title', '{' + tex(title) + '}']]
  if (authors.length) fields.push(['author', authors.map(a => /[\u3400-\u9fff]/.test(a) ? '{' + tex(a) + '}' : tex(a)).join(' and ')])
  if (paper.year) fields.push(['year', String(paper.year)])
  if (arxiv) fields.push(['eprint', tex(arxiv)], ['archivePrefix', 'arXiv'])
  else if (paper.doi) fields.push(['doi', tex(paper.doi)])
  fields.push(['url', tex(url)], ['urldate', accessed])
  return `@misc{paper${paper.year ?? ''}_${hash(paper.id)},\n${fields.map(([key, value]) => `  ${key} = {${value}}`).join(',\n')}\n}`
}
