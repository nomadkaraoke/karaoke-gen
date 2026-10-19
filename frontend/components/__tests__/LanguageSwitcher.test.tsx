/**
 * @jest-environment jsdom
 */
import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import LanguageSwitcher from '../LanguageSwitcher'

const replaceMock = jest.fn()
jest.mock('@/i18n/routing', () => ({
  useRouter: () => ({ replace: replaceMock }),
  usePathname: () => '/requests',
  routing: { locales: ['en', 'es', 'de'] },
}))

function pick(language: RegExp) {
  fireEvent.click(screen.getByRole('button', { expanded: false }))
  fireEvent.click(screen.getByRole('button', { name: language }))
}

describe('LanguageSwitcher', () => {
  beforeEach(() => {
    replaceMock.mockClear()
    localStorage.clear()
  })

  it('navigates to the same path in the chosen locale by default', () => {
    render(<LanguageSwitcher />)
    pick(/Deutsch/)
    expect(replaceMock).toHaveBeenCalledWith('/requests', { locale: 'de' })
    expect(localStorage.getItem('locale')).toBe('de')
  })

  it('hands the choice to onSelectLocale instead of routing when provided', () => {
    const onSelectLocale = jest.fn()
    render(<LanguageSwitcher onSelectLocale={onSelectLocale} />)
    pick(/Espa/)
    expect(onSelectLocale).toHaveBeenCalledWith('es')
    expect(replaceMock).not.toHaveBeenCalled()
    expect(localStorage.getItem('locale')).toBe('es')
  })
})
