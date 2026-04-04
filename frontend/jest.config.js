const nextJest = require('next/jest')

const createJestConfig = nextJest({
  // Provide the path to your Next.js app to load next.config.js and .env files in your test environment
  dir: './',
})

// Add any custom config to be passed to Jest
const customJestConfig = {
  setupFilesAfterEnv: ['<rootDir>/jest.setup.js'],
  testEnvironment: 'jest-environment-jsdom',
  moduleNameMapper: {
    '^@/(.*)$': '<rootDir>/$1',
    '^nanoid$': '<rootDir>/node_modules/nanoid/index.cjs',
  },
  testMatch: [
    '**/__tests__/**/*.test.[jt]s?(x)',
  ],
  testPathIgnorePatterns: [
    '/node_modules/',
    '/e2e/',  // E2E tests run via Playwright, not Jest
  ],
  // Allow Jest to transform ESM packages (next-intl uses ESM exports)
  transformIgnorePatterns: [
    '/node_modules/(?!(next-intl|use-intl)/)',
  ],
  collectCoverageFrom: [
    'components/**/*.{js,jsx,ts,tsx}',
    'lib/**/*.{js,jsx,ts,tsx}',
    'app/**/*.{js,jsx,ts,tsx}',
    '!**/*.d.ts',
    '!**/node_modules/**',
    '!**/.next/**',
    '!**/out/**',
  ],
}

// createJestConfig is exported this way to ensure that next/jest can load the Next.js config which is async
module.exports = createJestConfig(customJestConfig)

