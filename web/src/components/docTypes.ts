import type { IconName } from './Icon'

export const DOC_TYPES: Record<string, { label: string; plural: string; icon: IconName }> = {
  contract: { label: 'Договор', plural: 'Договоры', icon: 'briefcase' },
  supplement: { label: 'Доп. соглашение', plural: 'Доп. соглашения', icon: 'doc' },
  regulation: { label: 'Регламент', plural: 'Регламенты', icon: 'book' },
  letter: { label: 'Письмо', plural: 'Письма и записки', icon: 'doc' },
  law: { label: 'Закон', plural: 'Законы и кодексы', icon: 'scale' },
}

export const DOC_TYPE_ORDER = ['contract', 'supplement', 'regulation', 'letter', 'law']

export function docType(type: string) {
  return DOC_TYPES[type] ?? { label: type, plural: type, icon: 'doc' as IconName }
}
