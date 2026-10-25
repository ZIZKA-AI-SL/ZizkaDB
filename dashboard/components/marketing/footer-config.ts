export type FooterLink = {
  label: string
  href: string
  external?: boolean
}

export type FooterColumn = {
  title: string
  links: FooterLink[]
}

const GITHUB = 'https://github.com/ZIZKA-AI-SL/ZizkaDB'
const WIKI = 'https://github.com/ZIZKA-AI-SL/ZizkaDB/wiki'
const ZIZKA_AI = 'https://zizka.ai'

export const ZIZKADB_FOOTER_COLUMNS: FooterColumn[] = [
  {
    title: 'Product',
    links: [
      { label: 'Docs', href: '/docs' },
      { label: 'EU AI Act', href: '/eu-ai-act' },
      { label: 'Pricing', href: '/#pricing' },
      { label: 'Compare', href: '/#compare' },
      { label: 'Trust & security', href: '/trust' },
      { label: 'API explorer', href: '/swagger' },
    ],
  },
  {
    title: 'Resources',
    links: [
      { label: 'Self-host (OSS)', href: GITHUB, external: true },
      { label: 'Wiki', href: WIKI, external: true },
      { label: 'Community', href: '/community' },
      { label: 'MCP setup', href: '/docs#mcp' },
    ],
  },
  {
    title: 'Legal',
    links: [
      { label: 'Terms', href: `${ZIZKA_AI}/terms`, external: true },
      { label: 'Privacy', href: '/privacy' },
      { label: 'Security', href: '/trust#security' },
      { label: 'Contact', href: 'mailto:founder@zizka.ai', external: true },
      { label: 'Responsible disclosure', href: 'mailto:founder@zizka.ai?subject=Security%20disclosure', external: true },
    ],
  },
]

export const ZIZKADB_COMPANY = {
  name: 'ZIZKA AI S.L.',
  location: 'Málaga, Spain',
  taxId: 'CIF B26956078',
  email: 'founder@zizka.ai',
  zizkaAiUrl: ZIZKA_AI,
}
