import { render, screen, waitFor } from '@testing-library/react'
import { SystemStatusModal } from '@/components/system-status-modal'
import type { SystemStatus } from '@/lib/api'

// Mock the API layer so the modal renders against controlled status payloads.
const getSystemStatus = jest.fn()
jest.mock('@/lib/api', () => ({
  api: {
    getSystemStatus: () => getSystemStatus(),
  },
}))

function baseStatus(overrides: Partial<SystemStatus['services']> = {}): SystemStatus {
  return {
    services: {
      frontend: { status: 'ok', version: '0.196.3' },
      backend: { status: 'ok', version: '0.196.3' },
      encoder: { status: 'ok', version: '0.196.3' },
      flacfetch: { status: 'ok', version: '0.21.1' },
      separator: { status: 'offline' },
      ...overrides,
    },
  }
}

describe('SystemStatusModal', () => {
  beforeEach(() => {
    getSystemStatus.mockReset()
  })

  it('shows the live encoding worker instance type on the primary (no fallback)', async () => {
    getSystemStatus.mockResolvedValue(
      baseStatus({
        encoder: {
          status: 'ok',
          version: '0.196.3',
          admin_details: {
            primary_vm: 'encoding-worker-a',
            active_vm: 'encoding-worker-a',
            active_zone: 'us-central1-c',
            active_machine_type: 'c4d-highcpu-32',
            on_fallback: false,
          },
        },
      }),
    )

    render(<SystemStatusModal open onClose={() => {}} />)

    // Instance type + serving VM are surfaced for the live worker.
    expect(await screen.findByText('c4d-highcpu-32')).toBeInTheDocument()
    expect(screen.getByText(/encoding-worker-a · us-central1-c/)).toBeInTheDocument()
    // No fallback badge/notice when serving the primary.
    expect(screen.queryByText('Fallback')).not.toBeInTheDocument()
  })

  it('flags a capacity fallback with the fallback badge and notice', async () => {
    getSystemStatus.mockResolvedValue(
      baseStatus({
        encoder: {
          status: 'ok',
          version: '0.196.3',
          admin_details: {
            primary_vm: 'encoding-worker-a',
            active_vm: 'encoding-worker-fallback-n2c',
            active_zone: 'us-central1-a',
            active_machine_type: 'n2-highcpu-32',
            on_fallback: true,
          },
        },
      }),
    )

    render(<SystemStatusModal open onClose={() => {}} />)

    expect(await screen.findByText('n2-highcpu-32')).toBeInTheDocument()
    expect(screen.getByText('Fallback')).toBeInTheDocument()
    expect(
      screen.getByText(/Primary capacity unavailable/i),
    ).toBeInTheDocument()
  })

  it('omits the live-worker section for non-admins (no admin_details)', async () => {
    getSystemStatus.mockResolvedValue(baseStatus())

    render(<SystemStatusModal open onClose={() => {}} />)

    // Encoder card still renders, but there is no live-worker detail.
    await waitFor(() => expect(getSystemStatus).toHaveBeenCalled())
    expect(screen.queryByText('Live worker:')).not.toBeInTheDocument()
    expect(screen.queryByText('Fallback')).not.toBeInTheDocument()
  })
})
