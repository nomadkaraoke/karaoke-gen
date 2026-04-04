// Learn more: https://github.com/testing-library/jest-dom
import '@testing-library/jest-dom'

// Mock next-intl — loads actual English strings so tests see real text
const enMessages = require('./messages/en.json')

function getNestedValue(obj, dottedKey) {
  return dottedKey.split('.').reduce((o, k) => (o && typeof o === 'object' ? o[k] : undefined), obj)
}

jest.mock('next-intl', () => ({
  useTranslations: (namespace) => {
    const messages = namespace
      ? getNestedValue(enMessages, namespace) || {}
      : enMessages
    const t = (key, params) => {
      const value = getNestedValue(messages, key)
      if (typeof value !== 'string') return key
      if (params) {
        return value.replace(/\{(\w+)(?:,\s*plural,\s*one\s*\{([^}]*)\}\s*other\s*\{([^}]*)\})?\}/g,
          (match, name, one, other) => {
            if (one !== undefined && other !== undefined) {
              return params[name] === 1 ? one : other
            }
            return params[name] !== undefined ? String(params[name]) : match
          })
      }
      return value
    }
    t.rich = (key, params) => t(key, params)
    t.raw = (key) => getNestedValue(messages, key) || key
    return t
  },
  useLocale: () => 'en',
  useMessages: () => enMessages,
  NextIntlClientProvider: ({ children }) => children,
}))

// Mock @/i18n/routing — locale-aware navigation primitives
jest.mock('@/i18n/routing', () => {
  const React = require('react')
  return {
    routing: {
      locales: [
        'en', 'es', 'de', 'pt', 'fr', 'ja', 'ko', 'zh', 'it', 'nl',
        'pl', 'tr', 'ru', 'th', 'id', 'vi', 'tl', 'hi', 'ar', 'sv',
        'nb', 'da', 'fi', 'cs', 'ro', 'hu', 'el', 'he', 'ms', 'uk',
        'hr', 'sk', 'ca',
      ],
      defaultLocale: 'en',
    },
    Link: ({ children, href, ...props }) =>
      React.createElement('a', { href, ...props }, children),
    redirect: jest.fn(),
    usePathname: () => '/',
    useRouter: () => ({
      push: jest.fn(),
      replace: jest.fn(),
      prefetch: jest.fn(),
      back: jest.fn(),
      forward: jest.fn(),
      refresh: jest.fn(),
    }),
  }
})

// Mock window.matchMedia
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: jest.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: jest.fn(),
    removeListener: jest.fn(),
    addEventListener: jest.fn(),
    removeEventListener: jest.fn(),
    dispatchEvent: jest.fn(),
  })),
})

// Mock IntersectionObserver
global.IntersectionObserver = class IntersectionObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  takeRecords() {
    return []
  }
  unobserve() {}
}

// Mock ResizeObserver
global.ResizeObserver = class ResizeObserver {
  constructor() {}
  disconnect() {}
  observe() {}
  unobserve() {}
}

