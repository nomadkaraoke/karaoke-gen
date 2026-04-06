import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { NextIntlClientProvider } from 'next-intl';
import QRCodeDialog from '../QRCodeDialog';

// Mock qr-code-styling — it requires canvas/DOM APIs not available in jsdom
const mockAppend = jest.fn();
const mockUpdate = jest.fn();
const mockDownload = jest.fn().mockResolvedValue(undefined);

jest.mock('qr-code-styling', () => {
  return jest.fn().mockImplementation(() => ({
    append: mockAppend,
    update: mockUpdate,
    download: mockDownload,
  }));
});

// Load real English messages for the referrals namespace
const messages = require('../../../messages/en.json');

function renderDialog(props: Partial<React.ComponentProps<typeof QRCodeDialog>> = {}) {
  return render(
    <NextIntlClientProvider locale="en" messages={messages}>
      <QRCodeDialog
        referralUrl="https://nomadkaraoke.com/r/testcode"
        open={true}
        onOpenChange={jest.fn()}
        {...props}
      />
    </NextIntlClientProvider>
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  localStorage.clear();
});

describe('QRCodeDialog', () => {
  it('renders the dialog title and download buttons when open', () => {
    renderDialog();
    expect(screen.getByText('QR Code Generator')).toBeInTheDocument();
    expect(screen.getByText('Download PNG')).toBeInTheDocument();
    expect(screen.getByText('Download SVG')).toBeInTheDocument();
  });

  it('does not render when closed', () => {
    renderDialog({ open: false });
    expect(screen.queryByText('QR Code Generator')).not.toBeInTheDocument();
  });

  it('renders all dot style options', () => {
    renderDialog();
    // "Square" appears multiple times (dot style, corner frame, corner dot) — use getAllByText
    expect(screen.getAllByText('Square').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('Rounded')).toBeInTheDocument();
    expect(screen.getByText('Dots')).toBeInTheDocument();
    expect(screen.getByText('Classy')).toBeInTheDocument();
    expect(screen.getByText('Classy Rounded')).toBeInTheDocument();
    expect(screen.getAllByText('Extra Rounded').length).toBeGreaterThanOrEqual(1);
  });

  it('renders corner frame and corner dot style sections', () => {
    renderDialog();
    expect(screen.getByText('Corner Frame')).toBeInTheDocument();
    expect(screen.getByText('Corner Dot')).toBeInTheDocument();
  });

  it('renders center logo options', () => {
    renderDialog();
    expect(screen.getByText('Center Logo')).toBeInTheDocument();
    expect(screen.getByText('None')).toBeInTheDocument();
    expect(screen.getByText('Nomad Karaoke')).toBeInTheDocument();
    expect(screen.getByText('Microphone')).toBeInTheDocument();
    expect(screen.getByText('Music')).toBeInTheDocument();
  });

  it('renders color picker inputs', () => {
    renderDialog();
    expect(screen.getByText('Foreground')).toBeInTheDocument();
    expect(screen.getByText('Background')).toBeInTheDocument();
    // Color inputs
    const colorInputs = screen.getAllByDisplayValue('#000000');
    expect(colorInputs.length).toBeGreaterThanOrEqual(1);
  });

  it('saves style prefs to localStorage on change', async () => {
    jest.useFakeTimers();
    renderDialog();
    // Click "Rounded" dot style
    fireEvent.click(screen.getByText('Rounded'));
    // Flush the debounce timer
    act(() => { jest.runAllTimers(); });
    const saved = JSON.parse(localStorage.getItem('nk-qr-style-prefs') || '{}');
    expect(saved.dotStyle).toBe('rounded');
    jest.useRealTimers();
  });

  it('restores style prefs from localStorage on open', () => {
    localStorage.setItem('nk-qr-style-prefs', JSON.stringify({
      dotStyle: 'dots',
      cornerSquareStyle: 'dot',
      cornerDotStyle: 'dot',
      fgColor: '#ff0000',
      bgColor: '#00ff00',
      logo: 'nomad',
    }));
    renderDialog();
    // The foreground color input should have the saved value
    const fgInput = screen.getByDisplayValue('#ff0000');
    expect(fgInput).toBeInTheDocument();
  });

  it('calls download with PNG extension when Download PNG clicked', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Download PNG'));
    await waitFor(() => {
      expect(mockDownload).toHaveBeenCalledWith({
        name: 'referral-qr',
        extension: 'png',
      });
    });
  });

  it('calls download with SVG extension when Download SVG clicked', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByText('Download SVG'));
    await waitFor(() => {
      expect(mockDownload).toHaveBeenCalledWith({
        name: 'referral-qr',
        extension: 'svg',
      });
    });
  });

  it('renders flyer generation controls', () => {
    renderDialog();
    expect(screen.getByText('Generate Flyer')).toBeInTheDocument();
    expect(screen.getByText('Light')).toBeInTheDocument();
    expect(screen.getByText('Dark')).toBeInTheDocument();
    expect(screen.getByText('Flyer Theme')).toBeInTheDocument();
  });
});
